# grenergy-bms-py

This is an independent, unofficial project. It is not affiliated with or
endorsed by the app's developer or the BMS manufacturer. Use it on hardware
you own.

## What it can do

Basically everything the app's screens can do:

- Live monitoring: pack voltage, current, remaining/nominal capacity, SOC%,
  cycle count, per-cell voltages, per-cell internal resistance, temperatures,
  active balancing cells, and all protection alarm flags.
- Control: turn the whole battery on/off (charge + discharge FETs), or just
  one side; toggle balancing; reset capacity, clear history, clear a tripped
  protection state, reboot, sleep/deep-sleep.
- Device info: manufacturer, model, serial, hardware version, IC type.
- Settings: read/write protection-voltage thresholds (pack and per-cell,
  over/under voltage), temperature protection thresholds, sense resistor
  value, cell count, report interval, current-detect threshold, sleep timer.
- Rename the BLE device, clear the BMS's internal password.
- A `read_all()` call that pulls every one of the above in one go, similar
  to flipping through every tab in the app.

## Install

```
pip install -r requirements.txt
```

Needs Python 3.9+ and [bleak](https://github.com/hbldh/bleak), which works
on Windows, macOS and Linux without extra drivers.

## Quick start

```
python cli.py scan
python cli.py status  AA:BB:CC:DD:EE:FF
python cli.py on      AA:BB:CC:DD:EE:FF
python cli.py off     AA:BB:CC:DD:EE:FF
```

Or use it as a library:

```python
import asyncio
from grenergy_bms import GrenergyBMS

async def main():
    async with GrenergyBMS("AA:BB:CC:DD:EE:FF") as bms:
        info = await bms.read_basic_info()
        print(info.total_voltage, info.current, info.rsoc_percent)
        await bms.turn_battery_off()

asyncio.run(main())
```

## About the app's "password"

The app shows a dialog asking for a fixed code (`JLNBATTERY`) before letting
you tap the charge/discharge OFF buttons. That check is purely client-side
in the app's UI - it's never sent over Bluetooth. There is no real BLE
pairing PIN and no device-side authentication in this protocol, so this
client doesn't need one either.

## A note on safety

Turning FETs on/off or writing protection thresholds changes real electrical
behavior on a live battery pack. A few of the parameter registers in
`grenergy_bms.py` have scale factors inferred from the decompiled Android
code where the original formula looked ambiguous or possibly buggy - those
are only exposed through the raw `read_param()` / `write_param()` calls
rather than a named helper, so nothing here silently guesses at a value it
isn't reasonably sure about. Read back a value after writing it, and when in
doubt, cross-check against the app before relying on a setting.

## License

MIT, see [LICENSE](LICENSE).


## Contact

Telegram: @G3N3X_07
