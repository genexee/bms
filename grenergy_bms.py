# MIT License - Copyright (c) 2026 genex, see LICENSE for full text

from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field
from typing import Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

log = logging.getLogger("grenergy_bms")

SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"

READ = 0xA5
WRITE = 0x5A


class BMSError(Exception):
    # base class for everything this client raises
    pass


class ChecksumError(BMSError):
    # response frame failed checksum validation
    pass


class ResponseError(BMSError):
    # response frame was malformed, or device reported a non-zero status
    pass


class BMSTimeoutError(BMSError):
    # no complete, valid response arrived before the timeout
    pass


# @en low-level frame codec, build and parse the raw byte frames

def _checksum16(cmd: int, content: bytes) -> bytes:
    total = sum(content) + len(content) + cmd
    ck = (-total) & 0xFFFF
    return struct.pack(">H", ck)


def build_frame(rw_mode: int, cmd: int, content: bytes = b"") -> bytes:
    # builds a standard DD ... 77 request frame
    if len(content) > 255:
        raise ValueError("content too long for a single frame")
    frame = bytearray([0xDD, rw_mode, cmd, len(content)])
    frame += content
    frame += _checksum16(cmd, content)
    frame.append(0x77)
    return bytes(frame)


def _checksum8(cmd: int, content: bytes) -> int:
    return (sum(content) + len(content) + cmd) & 0xFF


def build_name_frame(cmd: int, content: bytes = b"") -> bytes:
    # builds an FF AA frame, only used for the BLE device-rename command
    frame = bytearray([0xFF, 0xAA, cmd, len(content)])
    frame += content
    frame.append(_checksum8(cmd, content))
    return bytes(frame)


def expected_frame_length(buf: bytes) -> Optional[int]:
    # given a buffer starting with a frame header, returns how many bytes
    # the full frame should be, or None if we don't know yet
    if not buf:
        return None
    if buf[0] == 0xDD:
        if len(buf) < 4:
            return None
        return buf[3] + 7  # 4 header bytes + content + 2 checksum + 1 trailer
    if buf[0] == 0xFF:
        if len(buf) < 4:
            return None
        return buf[3] + 5  # 4 header bytes + content + 1 checksum, no trailer
    return None  # unrecognized leading byte, caller should resync


def parse_frame(raw: bytes) -> tuple[int, bytes]:
    # validates and splits a standard (0xDD ... 0x77) response frame
    # returns (status_byte, content_bytes)
    if len(raw) < 7 or raw[0] != 0xDD or raw[-1] != 0x77:
        raise ResponseError(f"malformed frame: {raw.hex()}")
    status = raw[2]
    length = raw[3]
    content = raw[4:4 + length]
    if len(content) != length:
        raise ResponseError(f"truncated frame: {raw.hex()}")
    ck_expected = _checksum16(status, content)
    ck_actual = raw[4 + length:6 + length]
    if ck_expected != ck_actual:
        raise ChecksumError(
            f"checksum mismatch: expected {ck_expected.hex()} got {ck_actual.hex()} ({raw.hex()})"
        )
    return status, content


def parse_name_frame(raw: bytes) -> tuple[int, bytes]:
    # validates and splits an FF AA response frame
    if len(raw) < 5 or raw[0] != 0xFF:
        raise ResponseError(f"malformed name-frame: {raw.hex()}")
    status = raw[2]
    length = raw[3]
    content = raw[4:4 + length]
    ck_expected = _checksum8(status, content)
    ck_actual = raw[4 + length]
    if ck_expected != ck_actual:
        raise ChecksumError(f"checksum mismatch in name-frame: {raw.hex()}")
    return status, content


def _s16(data: bytes) -> int:
    # signed big-endian 16-bit int, matches Java's (b0<<8)+(b1&0xFF) idiom
    return struct.unpack(">h", data)[0]


def _u16(data: bytes) -> int:
    return struct.unpack(">H", data)[0]


# @en parsed data models, turn raw content bytes into something usable

PROTECTION_FLAG_NAMES = [
    "cell_overvoltage", "cell_undervoltage", "pack_overvoltage", "pack_undervoltage",
    "charge_overtemp", "charge_undertemp", "discharge_overtemp", "discharge_undertemp",
    "charge_overcurrent", "discharge_overcurrent", "short_circuit", "ic_error",
    "software_lock_mos", "charge_overtime",
]


@dataclass
class BasicInfo:
    total_voltage: float           # V
    current: float                 # A, positive = charging, negative = discharging
    remaining_capacity_ah: float   # Ah
    nominal_capacity_ah: float     # Ah
    cycle_count: int
    production_date: str           # "YYYY-M-D"
    balance_states: list[bool]     # per-cell balancing active flags, index 0 = cell 1
    protection_flags: dict[str, bool]
    version: str
    rsoc_percent: int
    charge_fet_on: bool
    discharge_fet_on: bool
    cell_count: int
    ntc_count: int
    temperatures_c: list[float]
    humidity_percent: Optional[int] = None
    balance_current_a: Optional[float] = None
    learned_capacity_ah: Optional[float] = None


def parse_basic_info(content: bytes) -> BasicInfo:
    total_voltage = _s16(content[0:2]) / 100.0
    current = _s16(content[2:4]) / 100.0
    remaining_capacity_ah = _u16(content[4:6]) / 100.0
    nominal_capacity_ah = _u16(content[6:8]) / 100.0
    cycle_count = _s16(content[8:10])

    date_packed = _s16(content[10:12])
    year = (date_packed >> 9) + 2000
    month = (date_packed >> 5) & 0xF
    day = date_packed & 0x1F
    production_date = f"{year}-{month}-{day}"

    b12, b13, b14, b15 = content[12], content[13], content[14], content[15]
    protection_hi, protection_lo = content[16], content[17]
    version_byte = content[18]
    rsoc = content[19]
    fet_state = content[20]
    cell_count = content[21]
    ntc_count = content[22]

    protection_flags = {}
    for i, name in enumerate(PROTECTION_FLAG_NAMES):
        byte_val, bit = (protection_lo, i) if i < 8 else (protection_hi, i - 8)
        protection_flags[name] = bool((byte_val >> bit) & 1)

    balance_states = []
    for i in range(cell_count):
        if i < 8:
            byte_val = b13
        elif i < 16:
            byte_val = b12
        elif i < 24:
            byte_val = b15
        else:
            byte_val = b14
        balance_states.append(bool((byte_val >> (i % 8)) & 1))

    temps = []
    idx = 23
    for _ in range(ntc_count):
        raw = _u16(content[idx:idx + 2])
        temps.append((raw - 2731) / 10.0)
        idx += 2

    info = BasicInfo(
        total_voltage=total_voltage,
        current=current,
        remaining_capacity_ah=remaining_capacity_ah,
        nominal_capacity_ah=nominal_capacity_ah,
        cycle_count=cycle_count,
        production_date=production_date,
        balance_states=balance_states,
        protection_flags=protection_flags,
        version=f"{version_byte >> 4:X}.{version_byte & 0xF:X}",
        rsoc_percent=rsoc,
        charge_fet_on=bool(fet_state & 0x1),
        discharge_fet_on=bool(fet_state & 0x2),
        cell_count=cell_count,
        ntc_count=ntc_count,
        temperatures_c=temps,
    )

    if len(content) > ntc_count * 2 + 27:
        base = 23 + ntc_count * 2
        info.humidity_percent = content[base]
        info.learned_capacity_ah = _u16(content[base + 3:base + 5]) / 100.0
        info.balance_current_a = _s16(content[base + 5:base + 7]) / 100.0

    return info


@dataclass
class CellVoltage:
    cell_number: int    # 1-based
    voltage: float      # V
    is_min: bool = False
    is_max: bool = False


def parse_cell_voltages(content: bytes) -> list[CellVoltage]:
    cells = []
    for i in range(0, len(content) - 1, 2):
        cells.append(CellVoltage(cell_number=(i // 2) + 1, voltage=_s16(content[i:i + 2]) / 1000.0))
    if cells:
        min_cell = min(cells, key=lambda c: c.voltage)
        max_cell = max(cells, key=lambda c: c.voltage)
        if max_cell.voltage - min_cell.voltage >= 0.02:
            min_cell.is_min = True
            max_cell.is_max = True
    return cells


def parse_resistances(content: bytes) -> list[float]:
    # per-cell internal resistance in milliohms
    return [_u16(content[i:i + 2]) / 10.0 for i in range(0, len(content) - 1, 2)]


PROTECTION_COUNTER_NAMES = [
    "short_circuit", "charge_overcurrent", "discharge_overcurrent",
    "cell_overvoltage", "cell_undervoltage", "charge_overtemp",
    "discharge_undertemp_1", "discharge_overtemp", "discharge_undertemp_2",
    "pack_overvoltage", "pack_undervoltage",
]


def parse_protection_counters(content: bytes) -> dict[str, int]:
    values = [_u16(content[i:i + 2]) for i in range(0, min(len(content), 22), 2)]
    return dict(zip(PROTECTION_COUNTER_NAMES, values))


# @en control-command content bytes (cmd 0x0A, write)

class ControlOp:
    RESET_CAPACITY = bytes([1, 0])
    CLEAR_RECORD = bytes([2, 0])
    REBOOT = bytes([3, 0])
    CLEAR_PROTECTION = bytes([4, 0])
    SLEEP = bytes([5, 0])
    DEEP_SLEEP = bytes([6, 0])
    OPEN_BALANCE = bytes([7, 0])


# registers used with cmd 0xFA (read/write), writes get wrapped in
# factory-mode open/close. confidence varies, see the notes at the top of
# this file. values only listed here where the decompiled scale factor
# looked unambiguous.
class ParamReg:
    NOMINAL_CAPACITY = 0       # count 2: [Ah*10, Ah*10] (nominal, cycle capacity)
    FULL_CHARGE_VOLTAGE = 2    # count 1: V*100
    PACK_OV_PROTECT = 16       # count 1: V*100
    PACK_OV_RELEASE = 17       # count 1: V*100
    PACK_UV_PROTECT = 18       # count 1: V*100
    PACK_UV_RELEASE = 19       # count 1: V*100
    CELL_OV_PROTECT = 20       # count 1: mV (raw)
    CELL_OV_RELEASE = 21       # count 1: mV (raw)
    CELL_UV_PROTECT = 22       # count 1: mV (raw)
    CELL_UV_RELEASE = 23       # count 1: mV (raw)
    BALANCE_START_VOLTAGE = 26 # count 1: mV (raw)
    BALANCE_ACCURACY = 27      # count 1: mV (raw)
    SENSE_RESISTOR = 28        # count 1: milliohm*10
    FUNCTION_CONFIG = 29       # count 1: raw bitfield (bit2=balance enable, bit3=balance mode)
    CELL_COUNT = 31            # count 1: raw int
    TEMP_PROTECTION = 8        # count 8: raw, decode via (raw-2731)/10.0 -> Celsius
    REPORT_INTERVAL = 113      # count 1: seconds, raw int (600-65535)
    CURRENT_DETECT_THRESHOLD = 121  # count 1: mA, raw int (50-5000)
    SLEEP_TIMER = 128          # count 1: seconds, raw int (10-255)
    RATED_INFO = 117           # count 4: raw, see read_rated_info()
    BMS_SERIAL_TEXT = 170      # count 6: GB2312 text
    BMS_MODEL_TEXT = 176       # count 8: GB2312 text
    # these have unclear/possibly-buggy scale factors in the decompiled UI
    # code (nibble-packed IC-lookup-table indices, or inconsistent
    # unit-conversion helpers) - use read_param/write_param directly and
    # cross-check against the app if you need them
    OC_SC_CONFIG = 40          # count 4 (nibble-packed OC/SC index + delay)
    DELAY_CLUSTER_1 = 48       # count 4
    OC_DELAY_CLUSTER = 52      # count 4
    TEMP_DELAY_CLUSTER = 44    # count 4


# @en BMS client, async client for a Grenergy/JBD-protocol BMS over BLE
#
# usage:
#   async with GrenergyBMS(address) as bms:
#       info = await bms.read_basic_info()
#       cells = await bms.read_cell_voltages()

class GrenergyBMS:

    def __init__(self, address_or_device, response_timeout: float = 5.0):
        self._address_or_device = address_or_device
        self._client: Optional[BleakClient] = None
        self._response_timeout = response_timeout
        self._buf = bytearray()
        self._pending: Optional[asyncio.Future] = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "GrenergyBMS":
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    @staticmethod
    async def scan(timeout: float = 5.0) -> list[BLEDevice]:
        # scans for BLE devices advertising the BMS service UUID
        return await BleakScanner.discover(timeout=timeout, service_uuids=[SERVICE_UUID])

    async def connect(self) -> None:
        self._client = BleakClient(self._address_or_device)
        await self._client.connect()
        await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
        log.info("connected to %s", self._address_or_device)

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass
            await self._client.disconnect()
        self._client = None

    def _on_notify(self, _handle, data: bytearray) -> None:
        self._buf += data
        needed = expected_frame_length(bytes(self._buf))
        if needed is None:
            if self._buf and self._buf[0] not in (0xDD, 0xFF):
                del self._buf[0]  # resync: drop garbage leading byte
            return
        if len(self._buf) < needed:
            return
        raw = bytes(self._buf[:needed])
        del self._buf[:needed]
        if self._pending and not self._pending.done():
            self._pending.set_result(raw)

    async def _transact(self, frame: bytes, name_frame: bool = False) -> tuple[int, bytes]:
        if not self._client or not self._client.is_connected:
            raise BMSError("not connected")
        async with self._lock:
            self._buf.clear()
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            log.debug("-> %s", frame.hex())
            await self._client.write_gatt_char(WRITE_CHAR_UUID, frame, response=False)
            try:
                raw = await asyncio.wait_for(self._pending, timeout=self._response_timeout)
            except asyncio.TimeoutError as e:
                raise BMSTimeoutError(f"no response within {self._response_timeout}s") from e
            finally:
                self._pending = None
            log.debug("<- %s", raw.hex())
            return parse_name_frame(raw) if name_frame else parse_frame(raw)

    async def _read(self, cmd: int, content: bytes = b"") -> bytes:
        status, resp_content = await self._transact(build_frame(READ, cmd, content))
        if status != 0:
            raise ResponseError(f"device returned error status {status} for read cmd {cmd:#x}")
        return resp_content

    async def _write(self, cmd: int, content: bytes = b"") -> bytes:
        status, resp_content = await self._transact(build_frame(WRITE, cmd, content))
        if status != 0:
            raise ResponseError(f"device returned error status {status} for write cmd {cmd:#x}")
        return resp_content

    # @en monitoring, read-only, no factory mode needed

    async def read_basic_info(self) -> BasicInfo:
        return parse_basic_info(await self._read(0x03))

    async def read_cell_voltages(self) -> list[CellVoltage]:
        return parse_cell_voltages(await self._read(0x04))

    async def read_hardware_version(self) -> bytes:
        return await self._read(0x05)

    async def read_resistances(self) -> list[float]:
        return parse_resistances(await self._read(0xF6))

    async def read_protection_counters(self) -> dict[str, int]:
        return parse_protection_counters(await self._read(0xAA))

    async def read_manufacturer(self) -> str:
        content = await self._read(0xA0)
        return content.decode("gb2312", errors="replace").strip()

    async def read_ic_type(self) -> int:
        content = await self._read(0x00, bytes([0x00, 0x00]))
        return _u16(content[0:2])

    async def read_ntc_config(self) -> list[bool]:
        content = await self._read(0x2E)
        return [bool((content[1] >> n) & 1) for n in range(8)]

    async def read_rated_info(self) -> dict[str, int]:
        content = await self.read_param(ParamReg.RATED_INFO, 4)
        return {
            "rated_charging_voltage_v": _u16(content[0:2]) / 10.0,
            "rated_charging_current_raw": _u16(content[2:4]),
            "rated_discharge_current_raw": _u16(content[4:6]),
            "rated_discharge_power_raw": _u16(content[6:8]),
        }

    async def read_serial_text(self) -> str:
        content = await self.read_param(ParamReg.BMS_SERIAL_TEXT, 6)
        return content.decode("gb2312", errors="replace").strip()

    async def read_model_text(self) -> str:
        content = await self.read_param(ParamReg.BMS_MODEL_TEXT, 8)
        return content.decode("gb2312", errors="replace").strip()

    # @en FET / charge-discharge control (cmd 0xFB, write, no factory mode)

    async def set_charge_fet(self, on: bool) -> None:
        # turns the charge FET on or off
        await self._write(0xFB, bytes([1, 0 if on else 1]))

    async def set_discharge_fet(self, on: bool) -> None:
        # turns the discharge FET on or off
        await self._write(0xFB, bytes([0, 0 if on else 1]))

    async def turn_battery_on(self) -> None:
        await self.set_charge_fet(True)
        await self.set_discharge_fet(True)

    async def turn_battery_off(self) -> None:
        await self.set_charge_fet(False)
        await self.set_discharge_fet(False)

    # @en simple control ops (cmd 0x0A, write)

    async def reset_capacity(self) -> None:
        await self._write(0x0A, ControlOp.RESET_CAPACITY)

    async def clear_history(self) -> None:
        await self._write(0x0A, ControlOp.CLEAR_RECORD)

    async def reboot(self) -> None:
        await self._write(0x0A, ControlOp.REBOOT)

    async def clear_protection(self) -> None:
        await self._write(0x0A, ControlOp.CLEAR_PROTECTION)

    async def sleep(self) -> None:
        await self._write(0x0A, ControlOp.SLEEP)

    async def deep_sleep(self) -> None:
        await self._write(0x0A, ControlOp.DEEP_SLEEP)

    # balancing (cmd 0xF4, write)

    async def set_balance(self, on: bool) -> None:
        await self._write(0xF4, bytes([1 if on else 0]))

    # device housekeeping

    async def clear_bms_password(self) -> None:
        await self._write(0x09, b"\x06J1B2D4")

    async def rename_device(self, new_name: str) -> None:
        status, _ = await self._transact(
            build_name_frame(0x07, new_name.encode("ascii")), name_frame=True
        )
        if status != 0:
            raise ResponseError(f"device returned error status {status} for rename")

    # @en factory-mode gated parameter read/write (cmd 0xFA)

    async def open_factory_mode(self) -> None:
        await self._write(0x00, bytes([0x56, 0x78]))

    async def close_factory_mode(self) -> None:
        await self._write(0x01, bytes([0x00, 0x00]))

    async def read_param(self, reg_addr: int, count: int) -> bytes:
        content = bytes([(reg_addr >> 8) & 0xFF, reg_addr & 0xFF, count])
        return await self._read(0xFA, content)

    async def write_param(self, reg_addr: int, value: bytes) -> None:

        await self.open_factory_mode()
        try:
            content = bytes([(reg_addr >> 8) & 0xFF, reg_addr & 0xFF, len(value)]) + value
            await self._write(0xFA, content)
        finally:
            await self.close_factory_mode()

    # @en convenience wrappers over write_param, scale factors confirmed

    async def set_pack_overvoltage_protect(self, volts: float) -> None:
        await self.write_param(ParamReg.PACK_OV_PROTECT, struct.pack(">H", round(volts * 100)))

    async def set_pack_overvoltage_release(self, volts: float) -> None:
        await self.write_param(ParamReg.PACK_OV_RELEASE, struct.pack(">H", round(volts * 100)))

    async def set_pack_undervoltage_protect(self, volts: float) -> None:
        await self.write_param(ParamReg.PACK_UV_PROTECT, struct.pack(">H", round(volts * 100)))

    async def set_pack_undervoltage_release(self, volts: float) -> None:
        await self.write_param(ParamReg.PACK_UV_RELEASE, struct.pack(">H", round(volts * 100)))

    async def set_cell_overvoltage_protect(self, volts: float) -> None:
        await self.write_param(ParamReg.CELL_OV_PROTECT, struct.pack(">H", round(volts * 1000)))

    async def set_cell_overvoltage_release(self, volts: float) -> None:
        await self.write_param(ParamReg.CELL_OV_RELEASE, struct.pack(">H", round(volts * 1000)))

    async def set_cell_undervoltage_protect(self, volts: float) -> None:
        await self.write_param(ParamReg.CELL_UV_PROTECT, struct.pack(">H", round(volts * 1000)))

    async def set_cell_undervoltage_release(self, volts: float) -> None:
        await self.write_param(ParamReg.CELL_UV_RELEASE, struct.pack(">H", round(volts * 1000)))

    async def read_temperature_protection(self) -> dict[str, float]:
        content = await self.read_param(ParamReg.TEMP_PROTECTION, 8)
        names = [
            "charge_high_temp", "charge_high_release", "charge_low_temp", "charge_low_release",
            "discharge_high_temp", "discharge_high_release", "discharge_low_temp", "discharge_low_release",
        ]
        return {
            name: (_u16(content[i * 2:i * 2 + 2]) - 2731) / 10.0
            for i, name in enumerate(names)
        }

    async def read_sense_resistor_milliohm(self) -> float:
        content = await self.read_param(ParamReg.SENSE_RESISTOR, 1)
        return _u16(content) / 10.0

    async def read_cell_count(self) -> int:
        content = await self.read_param(ParamReg.CELL_COUNT, 1)
        return _u16(content)

    async def read_report_interval_seconds(self) -> int:
        content = await self.read_param(ParamReg.REPORT_INTERVAL, 1)
        return _u16(content)

    async def set_report_interval_seconds(self, seconds: int) -> None:
        if not (600 <= seconds <= 65535):
            raise ValueError("report interval must be between 600 and 65535 seconds")
        await self.write_param(ParamReg.REPORT_INTERVAL, struct.pack(">H", seconds))

    async def read_current_detect_threshold_ma(self) -> int:
        content = await self.read_param(ParamReg.CURRENT_DETECT_THRESHOLD, 1)
        return _u16(content)

    async def set_current_detect_threshold_ma(self, milliamps: int) -> None:
        if not (50 <= milliamps <= 5000):
            raise ValueError("current-detect threshold must be between 50 and 5000 mA")
        await self.write_param(ParamReg.CURRENT_DETECT_THRESHOLD, struct.pack(">H", milliamps))

    async def read_sleep_timer_seconds(self) -> int:
        content = await self.read_param(ParamReg.SLEEP_TIMER, 1)
        return _u16(content)

    async def set_sleep_timer_seconds(self, seconds: int) -> None:
        if not (10 <= seconds <= 255):
            raise ValueError("sleep timer must be between 10 and 255 seconds")
        await self.write_param(ParamReg.SLEEP_TIMER, struct.pack(">H", seconds))

    # @en everything at once, mirroring every screen in the app

    async def read_all(self) -> dict:
        result: dict = {}

        async def _try(key, coro):
            try:
                result[key] = await coro
            except BMSError as e:
                result[key] = None
                result[f"{key}_error"] = str(e)

        # "Now" dashboard tab
        await _try("basic_info", self.read_basic_info())
        await _try("cell_voltages", self.read_cell_voltages())
        await _try("resistances_milliohm", self.read_resistances())

        # device / identification info
        await _try("hardware_version", self.read_hardware_version())
        await _try("manufacturer", self.read_manufacturer())
        await _try("model", self.read_model_text())
        await _try("serial", self.read_serial_text())
        await _try("ic_type", self.read_ic_type())

        # params tabs
        await _try("protection_counters", self.read_protection_counters())
        await _try("temperature_protection", self.read_temperature_protection())
        await _try("ntc_enabled", self.read_ntc_config())
        await _try("sense_resistor_milliohm", self.read_sense_resistor_milliohm())
        await _try("cell_count", self.read_cell_count())
        await _try("rated_info", self.read_rated_info())

        # system settings tab
        await _try("report_interval_seconds", self.read_report_interval_seconds())
        await _try("current_detect_threshold_ma", self.read_current_detect_threshold_ma())
        await _try("sleep_timer_seconds", self.read_sleep_timer_seconds())

        return result
