# Innova Butler Touch — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA integration](https://img.shields.io/badge/Home%20Assistant-2023.4%2B-blue.svg)](https://www.home-assistant.io/)

A Home Assistant custom integration for **Innova Butler Touch** controllers
managing **FCL485 fancoil** units over the **local HTTP API** — no cloud, no
account required.

---

## Features

| Capability | Detail |
|---|---|
| 🌡 Temperature | Read room temperature, set target setpoint |
| ❄️ / 🔥 HVAC modes | Off · Heating · Cooling (home-wide, matching the app) |
| 💨 Fan modes | **auto · notte · minimo · massimo** |
| 🕐 Schedule override (preset) | **none** (follow calendar) · **1h** · **1d** · **forever** |
| 🔌 Per-device power | Individual on/off per fancoil |
| 📡 Local polling | Configurable interval (default 60 s, min 10 s) |
| 🏠 Auto-discovery | All rooms and devices found automatically |

---

## Requirements

- Home Assistant 2023.4 or later
- Butler Touch controller reachable on your LAN (tested with firmware 3.1.x)
- No Innova cloud account needed

---

## Installation

### Via HACS (recommended)

1. In HACS → **⋮** → **Custom repositories**
2. Add `https://github.com/your-username/homeassistant-innova-butler-touch`  
   type **Integration**
3. Find **Innova Butler Touch** and click **Download**
4. Restart Home Assistant

### Manual

1. Copy the `custom_components/innova_butler_touch/` folder into  
   `<config>/custom_components/`
2. Restart Home Assistant

---

## Configuration

**Settings → Devices & Services → Add Integration → Innova Butler Touch**

| Field | Description |
|---|---|
| **IP address / hostname** | Local IP of your Butler Touch (e.g. `192.168.0.11`) |
| **Port** | HTTP port, almost always `80` |

The integration discovers all rooms and FCL485 fancoils automatically.

To adjust the polling interval: open the integration's **Configure** menu.

---

## Entities

One `climate` entity is created per fancoil. Entity attributes include:

| Attribute | Description |
|---|---|
| `hfm_type` | Active schedule override: `none / hour / day / forever` |
| `hfm_deadline` | Unix timestamp when override expires (`-1` = never) |
| `room` | Room name the device belongs to |
| `home_mode` | Home-wide mode (`1` = heating, `0` = auto) |
| `connection_status` | `1` = online, `0` = offline |
| `offset_heating` / `offset_cooling` | Temperature calibration offsets |

---

## How it works

The Butler Touch exposes a local PHP API at:

```
http://<host>/installedplugin/com.innova.ambiente/2.0/server/index.php
```

| Action | Method | Purpose |
|---|---|---|
| `getHomepage` | GET | Discover all rooms and devices |
| `detailDevice` | GET | Poll a single device state |
| `setSetPoint` | POST | Set target temperature |
| `setFunction` | POST | Set fan speed (1–4) |
| `powerOnDevice` | POST | Turn device on |
| `powerOffDevice` | POST | Turn device off |
| `setHfm` | POST | Set schedule override (none/hour/day/forever) |
| `setModeHome` | POST | Switch home-wide heating ↔ cooling mode |

### On/Off and schedule overrides

The Butler Touch pairs **power commands** with an **HFM** (Hold-Force-Mode)
override so the device knows how long to maintain the user's choice:

- **Turn ON** → `setHfm(forever)` + `powerOnDevice`
- **Turn OFF** → `setHfm(forever)` + `powerOffDevice`
- **Preset "1h"** → `setHfm(hour, 1)` — device returns to calendar after 1 h
- **Preset "none"** → `setHfm(none)` — device immediately follows its calendar

### Heating / Cooling mode

This is a **home-wide** setting. Changing one device's HVAC mode changes it
for all devices — exactly as the Butler Touch app behaves.

---

## Limitations

- Fan speed changes via `setFunction` work correctly.  
- The `preset_mode` entity attribute maps to the HFM override, not a Butler
  Touch "scene" or "programme".
- Heating/Cooling mode is home-wide — per-device mode is not available in the
  local API.

---

## License

MIT
