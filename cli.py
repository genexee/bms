# MIT License - Copyright (c) 2026 genex, see LICENSE for full text
#
# @en example command-line usage of grenergy_bms.GrenergyBMS.
#
#   python cli.py scan
#   python cli.py monitor       AA:BB:CC:DD:EE:FF
#   python cli.py status        AA:BB:CC:DD:EE:FF     (everything, like scrolling every app screen)
#   python cli.py on            AA:BB:CC:DD:EE:FF     (reconnect the pack, both FETs on)
#   python cli.py off           AA:BB:CC:DD:EE:FF     (disconnect the pack, both FETs off)
#   python cli.py fet-on        AA:BB:CC:DD:EE:FF charge|discharge
#   python cli.py fet-off       AA:BB:CC:DD:EE:FF charge|discharge
#   python cli.py balance       AA:BB:CC:DD:EE:FF on|off
#   python cli.py reset-capacity AA:BB:CC:DD:EE:FF

from __future__ import annotations

import asyncio
import sys

from grenergy_bms import GrenergyBMS

USAGE = (
    "usage: python cli.py <scan|monitor|status|on|off|fet-on|fet-off|balance|reset-capacity> [address] [...]\n"
    "see the comment block at the top of this file for full examples"
)


async def cmd_scan() -> None:
    devices = await GrenergyBMS.scan(timeout=5.0)
    if not devices:
        print("No BMS devices found (advertising service 0000ff00-...).")
        return
    for d in devices:
        print(f"{d.address}  {d.name}")


async def cmd_monitor(address: str) -> None:
    async with GrenergyBMS(address) as bms:
        info = await bms.read_basic_info()
        cells = await bms.read_cell_voltages()
        print(info)
        for c in cells:
            flag = " (min)" if c.is_min else " (max)" if c.is_max else ""
            print(f"  cell {c.cell_number}: {c.voltage:.3f} V{flag}")


async def cmd_status(address: str) -> None:
    async with GrenergyBMS(address) as bms:
        data = await bms.read_all()

    info = data.get("basic_info")
    if info:
        print("=== Now ===")
        print(f"  Pack voltage:     {info.total_voltage:.2f} V")
        print(f"  Current:          {info.current:+.2f} A")
        print(f"  Remaining:        {info.remaining_capacity_ah:.2f} Ah")
        print(f"  Nominal capacity: {info.nominal_capacity_ah:.2f} Ah")
        print(f"  SOC:              {info.rsoc_percent}%")
        print(f"  Cycles:           {info.cycle_count}")
        print(f"  Charge FET:       {'ON' if info.charge_fet_on else 'OFF'}")
        print(f"  Discharge FET:    {'ON' if info.discharge_fet_on else 'OFF'}")
        print(f"  Firmware version: {info.version}")
        print(f"  Production date:  {info.production_date}")
        print(f"  Temperatures:     {['%.1fC' % t for t in info.temperatures_c]}")
        active_protections = [k for k, v in info.protection_flags.items() if v]
        print(f"  Active alarms:    {active_protections or 'none'}")
        active_balance = [i + 1 for i, v in enumerate(info.balance_states) if v]
        print(f"  Balancing cells:  {active_balance or 'none'}")

    cells = data.get("cell_voltages")
    if cells:
        print("\n=== Cell voltages ===")
        for c in cells:
            flag = " (min)" if c.is_min else " (max)" if c.is_max else ""
            print(f"  cell {c.cell_number}: {c.voltage:.3f} V{flag}")

    res = data.get("resistances_milliohm")
    if res:
        print("\n=== Internal resistance ===")
        for i, r in enumerate(res, 1):
            print(f"  cell {i}: {r:.1f} mOhm")

    print("\n=== Device info ===")
    for key in ("hardware_version", "manufacturer", "model", "serial", "ic_type"):
        print(f"  {key}: {data.get(key)!r}")

    counters = data.get("protection_counters")
    if counters:
        print("\n=== Lifetime protection-trip counters ===")
        for k, v in counters.items():
            print(f"  {k}: {v}")

    temps = data.get("temperature_protection")
    if temps:
        print("\n=== Temperature protection thresholds ===")
        for k, v in temps.items():
            print(f"  {k}: {v:.1f} C")

    print("\n=== System settings ===")
    for key in (
        "ntc_enabled", "sense_resistor_milliohm", "cell_count", "rated_info",
        "report_interval_seconds", "current_detect_threshold_ma", "sleep_timer_seconds",
    ):
        print(f"  {key}: {data.get(key)}")

    errors = {k: v for k, v in data.items() if k.endswith("_error")}
    if errors:
        print("\n=== Not supported / failed on this device ===")
        for k, v in errors.items():
            print(f"  {k[:-6]}: {v}")


async def cmd_power(address: str, on: bool) -> None:
    async with GrenergyBMS(address) as bms:
        if on:
            await bms.turn_battery_on()
        else:
            await bms.turn_battery_off()
        print("ok")


async def cmd_fet(address: str, which: str, on: bool) -> None:
    async with GrenergyBMS(address) as bms:
        if which == "charge":
            await bms.set_charge_fet(on)
        elif which == "discharge":
            await bms.set_discharge_fet(on)
        else:
            raise SystemExit("which must be 'charge' or 'discharge'")
        print("ok")


async def cmd_balance(address: str, state: str) -> None:
    async with GrenergyBMS(address) as bms:
        await bms.set_balance(state == "on")
        print("ok")


async def cmd_reset_capacity(address: str) -> None:
    async with GrenergyBMS(address) as bms:
        await bms.reset_capacity()
        print("ok")


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE)
        raise SystemExit(1)
    action = sys.argv[1]
    if action == "scan":
        asyncio.run(cmd_scan())
    elif action == "monitor":
        asyncio.run(cmd_monitor(sys.argv[2]))
    elif action == "status":
        asyncio.run(cmd_status(sys.argv[2]))
    elif action == "on":
        asyncio.run(cmd_power(sys.argv[2], True))
    elif action == "off":
        asyncio.run(cmd_power(sys.argv[2], False))
    elif action == "fet-on":
        asyncio.run(cmd_fet(sys.argv[2], sys.argv[3], True))
    elif action == "fet-off":
        asyncio.run(cmd_fet(sys.argv[2], sys.argv[3], False))
    elif action == "balance":
        asyncio.run(cmd_balance(sys.argv[2], sys.argv[3]))
    elif action == "reset-capacity":
        asyncio.run(cmd_reset_capacity(sys.argv[2]))
    else:
        print(USAGE)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
