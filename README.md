# Innova Butler Touch – Home Assistant Integration

A Home Assistant custom integration for **Innova Butler Touch** controllers
managing **FCL485 fancoil** units via the **local HTTP API**.

Unlike the Innova Duepuntozero integration (which uses gRPC over the cloud),
this integration talks directly to your Butler Touch on your LAN — no cloud
dependency, no account required.

## What it does

- Discovers all fancoil devices registered on your Butler Touch
- Creates a `climate` entity for each one
- Polls device state locally (configurable interval, default 60 s)
- Supports:
  - Reading current room temperature and target setpoint
  - Setting target temperature
  - Switching between Heating / Cooling / Off modes
  - Fan mode display (read-only — the local API does not expose a fan control endpoint)

## Requirements

- Butler Touch on the same local network as Home Assistant
- Butler Touch firmware v3.x (tested with 3.1.8)
- No cloud account needed

## Installation

### Via HACS (recommended)

1. Add this repository as a custom HACS repository
2. Install **Innova Butler Touch**
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/innova_butler_touch` folder into your
   `<config>/custom_components/` directory
2. Restart Home Assistant

## Configuration

**Settings → Devices & Services → Add Integration → Innova Butler Touch**

Enter:
- **Host / IP address** of your Butler Touch (e.g. `192.168.0.11`)
- **Port** (default: `80`)

The integration will auto-discover all rooms and fancoil devices.

## Technical notes

The Butler Touch exposes a local PHP API at:
```
http://<host>/installedplugin/com.innova.ambiente/2.0/server/index.php
```

Key endpoints used:
| Action | Method | Purpose |
|---|---|---|
| `getHomepage` | GET | Get all rooms + devices |
| `detailDevice` | GET | Get a single device state |
| `setSetPoint` | POST | Set target temperature |
| `setHfm` | POST | Turn on/off (via HFM hold mechanism) |
| `setModeHome` | POST | Switch heating ↔ cooling |

### On/Off behaviour

The Butler Touch does not have a direct on/off command per device. Instead,
the integration uses the **HFM (Hold Force Mode)** mechanism:
- **Turn on**: `setHfm type=forever` → forces the device active indefinitely
- **Turn off**: `setHfm type=none` → returns control to the calendar schedule
  (which typically puts the device in standby outside programmed hours)

### Heating/Cooling mode

This is a **home-wide** setting — changing one device's mode changes all devices.
This matches how the Butler Touch app itself works.

## License

MIT
