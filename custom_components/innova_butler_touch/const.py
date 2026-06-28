"""Constants for the Innova Butler Touch integration."""

DOMAIN = "innova_butler_touch"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 60  # seconds

# Home modes (setModeHome)
HOME_MODE_HEATING = 1
HOME_MODE_COOLING = 2

# Fan function values (settings.function.value)
FAN_FUNCTION_AUTO = 1
FAN_FUNCTION_NIGHT = 2
FAN_FUNCTION_MIN = 3
FAN_FUNCTION_MAX = 4

FAN_FUNCTION_NAMES = {
    FAN_FUNCTION_AUTO: "auto",
    FAN_FUNCTION_NIGHT: "night",
    FAN_FUNCTION_MIN: "min",
    FAN_FUNCTION_MAX: "max",
}
FAN_FUNCTION_BY_NAME = {v: k for k, v in FAN_FUNCTION_NAMES.items()}
