"""Constants for the Innova Butler Touch integration."""

DOMAIN = "innova_butler_touch"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 60  # seconds

# ── Home modes (setModeHome / home.mode field) ────────────────────────────────
# Confirmed from JS source: getModeOptions() returns:
#   { value: 0, label: "MODE_HEATING" }  → riscaldamento
#   { value: 1, label: "MODE_COOLING"  } → raffreddamento
HOME_MODE_HEATING = 0
HOME_MODE_COOLING = 1

# ── Fan function values (settings.function.value / setFunction) ───────────────
# Confirmed from JS: getFunctionOptions()
FAN_FUNCTION_AUTO = 1
FAN_FUNCTION_NIGHT = 2   # notte / silent
FAN_FUNCTION_MIN = 3     # minimo
FAN_FUNCTION_MAX = 4     # massimo

# Map API value → HA fan-mode label (Italian names matching the app UI)
FAN_FUNCTION_TO_NAME: dict[int, str] = {
    FAN_FUNCTION_AUTO: "auto",
    FAN_FUNCTION_NIGHT: "notte",
    FAN_FUNCTION_MIN: "minimo",
    FAN_FUNCTION_MAX: "massimo",
}
FAN_NAME_TO_FUNCTION: dict[str, int] = {v: k for k, v in FAN_FUNCTION_TO_NAME.items()}

# ── HFM (Hold-Force-Mode) override types ─────────────────────────────────────
HFM_TYPE_NONE = "none"       # clear override, return to calendar
HFM_TYPE_HOUR = "hour"       # override for N hours
HFM_TYPE_DAY = "day"         # override for N×24 hours (value = total hours)
HFM_TYPE_FOREVER = "forever" # hold indefinitely (value = -1)

# ── Device standBy.value ─────────────────────────────────────────────────────
STANDBY_ACTIVE = 0   # device is on and running
STANDBY_IDLE = 1     # device is in standby (off per schedule or forced)
