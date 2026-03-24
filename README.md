# KIKUSUI-PMX-Controller

## PMX250-0.25A Controller

Simple Python controller for the Kikusui PMX250-0.25A over LAN (SCPI).

### Features

- Fixed IP control
- GUI / CUI support
- Output ON always starts at `0 V`
- Voltage changes in `1 V` steps every `0.1 s`
- Maximum voltage: `62 V`
- Maximum current: `10 mA`
- CSV logging every `10 s`
- Safe emergency stop
  - stop ramp
  - ramp down to `0 V`
  - output OFF

### Requirements

- Python 3
- `tkinter` for GUI mode
- Network access to the PMX SCPI port (`5025`)

### Run

```bash
python3 pmx_controller.py
```

### Configuration

Edit the global variables at the top of the script.

#### Switch GUI / CUI mode

```python
USE_GUI = True
```

- `True`: GUI mode
- `False`: CUI mode

#### Change IP address

```python
PMX_IP = "192.168.1.10"
```

Change this value to match your PMX IP address.

#### Example

```python
PMX_IP = "192.168.1.20"
USE_GUI = False
```

This example sets:

- IP address to `192.168.1.20`
- CUI mode

### GUI

The GUI includes:

- IP address
- Current voltage / current
- Target voltage input
- Set Voltage
- Emergency Stop
- Start/Stop Log
- ON/OFF

Behavior:

- ON starts output at `0 V`
- Voltage changes step by step to the target value
- When output is OFF, the target voltage field and Set Voltage button are disabled
- Emergency Stop ramps down safely to `0 V` and then turns output OFF

### Logging

Log files are saved as:

```text
yyyy-mmdd-HHMMSS.csv
```

Format:

```csv
timestamp,voltage_V,current_A,output_on
```

### Notes

- The script uses raw SCPI over TCP port `5025`
- Timing is software-based, so it is not intended for precise real-time control
- Designed for simple lab use with fixed settings

## Safety

This script controls a high-voltage power supply.  
Check wiring, grounding, current limit, and target voltage before use.
