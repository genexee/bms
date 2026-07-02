# MIT License - Copyright (c) 2026 genex, see LICENSE for full text
#
# @en interactive console app, just run it, no command-line arguments
# needed. scans for the BMS over bluetooth, asks you to pick one if it
# finds more than one, then shows a menu you drive by typing numbers.

from __future__ import annotations

import asyncio

from grenergy_bms import GrenergyBMS, BMSError


async def pick_device():
    print("scanning for BMS devices...")
    devices = await GrenergyBMS.scan(timeout=5.0)
    if not devices:
        print("no BMS devices found, make sure the battery/bluetooth module is powered on")
        return None
    if len(devices) == 1:
        d = devices[0]
        print(f"found one device: {d.name} ({d.address})")
        return d.address
    print("found multiple devices:")
    for i, d in enumerate(devices, 1):
        print(f"  {i}. {d.name}  ({d.address})")
    while True:
        choice = input(f"pick a device [1-{len(devices)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1].address
        print("invalid choice, try again")


def ask_yes_no(prompt):
    while True:
        ans = input(prompt + " [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("please type y or n")


def ask_number(prompt, cast=float):
    while True:
        raw = input(prompt).strip()
        try:
            return cast(raw)
        except ValueError:
            print("not a valid number, try again")


async def show_status(bms):
    data = await bms.read_all()

    info = data.get("basic_info")
    if info:
        print(f"pack voltage:    {info.total_voltage:.2f} V")
        print(f"current:         {info.current:+.2f} A")
        print(f"remaining:       {info.remaining_capacity_ah:.2f} Ah / {info.nominal_capacity_ah:.2f} Ah")
        print(f"soc:             {info.rsoc_percent}%")
        print(f"cycles:          {info.cycle_count}")
        print(f"charge fet:      {'ON' if info.charge_fet_on else 'OFF'}")
        print(f"discharge fet:   {'ON' if info.discharge_fet_on else 'OFF'}")
        print(f"temperatures:    {['%.1fC' % t for t in info.temperatures_c]}")
        active = [k for k, v in info.protection_flags.items() if v]
        print(f"active alarms:   {active or 'none'}")

    cells = data.get("cell_voltages")
    if cells:
        print("cell voltages:")
        for c in cells:
            flag = " (min)" if c.is_min else " (max)" if c.is_max else ""
            print(f"  cell {c.cell_number}: {c.voltage:.3f} V{flag}")

    res = data.get("resistances_milliohm")
    if res:
        print("resistance:      " + ", ".join(f"{r:.1f}mOhm" for r in res))

    print("manufacturer:   ", data.get("manufacturer"))
    print("model:          ", data.get("model"))

    errors = {k: v for k, v in data.items() if k.endswith("_error")}
    if errors:
        print("not supported on this device:", ", ".join(k[:-6] for k in errors))


async def show_live(bms):
    info = await bms.read_basic_info()
    cells = await bms.read_cell_voltages()
    print(f"pack voltage: {info.total_voltage:.2f} V   current: {info.current:+.2f} A   soc: {info.rsoc_percent}%")
    for c in cells:
        print(f"  cell {c.cell_number}: {c.voltage:.3f} V")


async def run_settings_menu(bms):
    while True:
        print()
        print("-- advanced settings --")
        print("1. set pack overvoltage protect (V)")
        print("2. set pack overvoltage release (V)")
        print("3. set pack undervoltage protect (V)")
        print("4. set pack undervoltage release (V)")
        print("5. set cell overvoltage protect (V)")
        print("6. set cell overvoltage release (V)")
        print("7. set cell undervoltage protect (V)")
        print("8. set cell undervoltage release (V)")
        print("9. set report interval (seconds)")
        print("10. set current-detect threshold (mA)")
        print("11. set sleep timer (seconds)")
        print("12. clear BMS password")
        print("0. back")
        choice = input("choose an option: ").strip()
        try:
            if choice == "1":
                v = ask_number("pack overvoltage protect volts: ")
                await bms.set_pack_overvoltage_protect(v)
            elif choice == "2":
                v = ask_number("pack overvoltage release volts: ")
                await bms.set_pack_overvoltage_release(v)
            elif choice == "3":
                v = ask_number("pack undervoltage protect volts: ")
                await bms.set_pack_undervoltage_protect(v)
            elif choice == "4":
                v = ask_number("pack undervoltage release volts: ")
                await bms.set_pack_undervoltage_release(v)
            elif choice == "5":
                v = ask_number("cell overvoltage protect volts: ")
                await bms.set_cell_overvoltage_protect(v)
            elif choice == "6":
                v = ask_number("cell overvoltage release volts: ")
                await bms.set_cell_overvoltage_release(v)
            elif choice == "7":
                v = ask_number("cell undervoltage protect volts: ")
                await bms.set_cell_undervoltage_protect(v)
            elif choice == "8":
                v = ask_number("cell undervoltage release volts: ")
                await bms.set_cell_undervoltage_release(v)
            elif choice == "9":
                v = ask_number("report interval seconds: ", int)
                await bms.set_report_interval_seconds(v)
            elif choice == "10":
                v = ask_number("current-detect threshold mA: ", int)
                await bms.set_current_detect_threshold_ma(v)
            elif choice == "11":
                v = ask_number("sleep timer seconds: ", int)
                await bms.set_sleep_timer_seconds(v)
            elif choice == "12":
                await bms.clear_bms_password()
                print("password cleared")
            elif choice == "0":
                return
            else:
                print("invalid option")
                continue
            print("done, saved on the BMS")
        except (BMSError, ValueError) as e:
            print(f"error: {e}")


def print_menu():
    print()
    print("-- grenergy bms --")
    print("1. view status (everything)")
    print("2. view live readings (voltage/current/cells)")
    print("3. turn battery ON  (charge + discharge)")
    print("4. turn battery OFF (charge + discharge)")
    print("5. charge switch on/off")
    print("6. discharge switch on/off")
    print("7. balance on/off")
    print("8. reset capacity")
    print("9. reboot BMS")
    print("10. clear protection (unlock after a trip)")
    print("11. rename device")
    print("12. advanced settings")
    print("0. exit")


async def run_menu(bms):
    while True:
        print_menu()
        choice = input("choose an option: ").strip()
        try:
            if choice == "1":
                await show_status(bms)
            elif choice == "2":
                await show_live(bms)
            elif choice == "3":
                await bms.turn_battery_on()
                print("battery turned ON")
            elif choice == "4":
                await bms.turn_battery_off()
                print("battery turned OFF")
            elif choice == "5":
                on = ask_yes_no("turn charge ON?")
                await bms.set_charge_fet(on)
                print("done")
            elif choice == "6":
                on = ask_yes_no("turn discharge ON?")
                await bms.set_discharge_fet(on)
                print("done")
            elif choice == "7":
                on = ask_yes_no("turn balance ON?")
                await bms.set_balance(on)
                print("done")
            elif choice == "8":
                if ask_yes_no("this resets the learned capacity, are you sure?"):
                    await bms.reset_capacity()
                    print("capacity reset")
            elif choice == "9":
                await bms.reboot()
                print("reboot command sent")
            elif choice == "10":
                await bms.clear_protection()
                print("protection cleared")
            elif choice == "11":
                name = input("new device name: ").strip()
                if name:
                    await bms.rename_device(name)
                    print("renamed, reconnect to see the new name")
            elif choice == "12":
                await run_settings_menu(bms)
            elif choice == "0":
                print("bye")
                return
            else:
                print("invalid option")
        except BMSError as e:
            print(f"error: {e}")


async def main():
    address = await pick_device()
    if not address:
        return
    print(f"connecting to {address} ...")
    async with GrenergyBMS(address) as bms:
        print("connected")
        await run_menu(bms)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
