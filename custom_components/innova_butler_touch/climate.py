"""Climate platform for Innova Butler Touch (FCL485 fancoils).

Each FCL485 fancoil unit registered on the Butler Touch becomes a
ClimateEntity with the following capabilities:

  HVAC modes
  ──────────
  off   → powerOffDevice  (device fully off, HFM=forever so it stays off)
  heat  → powerOnDevice   (device on, home mode forced to heating)
  cool  → powerOnDevice   (device on, home mode forced to cooling)
        Note: heat/cool changes the home-wide mode; this is how the
              Butler Touch app itself works.

  Fan modes
  ─────────
  auto · notte · minimo · massimo  → setFunction (1-4)

  Target temperature
  ──────────────────
  setSetPoint

  Preset modes (HFM overrides)
  ─────────────────────────────
  none     → clear HFM override, return to calendar schedule
  1h       → setHfm type=hour  value=1
  1d       → setHfm type=day   value=24
  forever  → setHfm type=forever value=-1

  Extra state attributes
  ──────────────────────
  hfm_type, hfm_deadline, room, connection_status, device_uid, home_mode
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ButlerTouchCoordinator
from .api import ButlerTouchApiError
from .const import (
    DOMAIN,
    FAN_FUNCTION_AUTO,
    FAN_FUNCTION_MAX,
    FAN_FUNCTION_MIN,
    FAN_FUNCTION_NIGHT,
    FAN_FUNCTION_TO_NAME,
    FAN_NAME_TO_FUNCTION,
    HFM_TYPE_DAY,
    HFM_TYPE_FOREVER,
    HFM_TYPE_HOUR,
    HFM_TYPE_NONE,
    HOME_MODE_COOLING,
    HOME_MODE_HEATING,
    STANDBY_ACTIVE,
    STANDBY_IDLE,
)

_LOGGER = logging.getLogger(__name__)

# ── HVAC mode mapping ────────────────────────────────────────────────────────
# API:  mode=0 → riscaldamento (heating)
#       mode=1 → raffreddamento (cooling)
# Confirmed from JS getModeOptions():
#   { value: 0, label: "MODE_HEATING" }
#   { value: 1, label: "MODE_COOLING" }

_HOME_MODE_TO_HVAC: dict[int, HVACMode] = {
    HOME_MODE_HEATING: HVACMode.HEAT,   # 0 → heat
    HOME_MODE_COOLING: HVACMode.COOL,   # 1 → cool
}
_HVAC_TO_HOME_MODE: dict[HVACMode, int] = {v: k for k, v in _HOME_MODE_TO_HVAC.items()}

# ── Fan modes ────────────────────────────────────────────────────────────────

_FAN_MODES = list(FAN_FUNCTION_TO_NAME.values())  # ["auto","notte","minimo","massimo"]

# ── Preset modes (HFM overrides) ─────────────────────────────────────────────

PRESET_NONE = "none"      # follow calendar
PRESET_1H = "1h"          # override 1 hour
PRESET_1D = "1d"          # override 1 day
PRESET_FOREVER = "forever"  # hold indefinitely

_PRESET_TO_HFM: dict[str, tuple[str, int]] = {
    PRESET_NONE:    (HFM_TYPE_NONE,    0),
    PRESET_1H:      (HFM_TYPE_HOUR,    1),
    PRESET_1D:      (HFM_TYPE_DAY,    24),
    PRESET_FOREVER: (HFM_TYPE_FOREVER, -1),
}
_HFM_TYPE_TO_PRESET: dict[str, str] = {
    HFM_TYPE_NONE:    PRESET_NONE,
    HFM_TYPE_HOUR:    PRESET_1H,
    HFM_TYPE_DAY:     PRESET_1D,
    HFM_TYPE_FOREVER: PRESET_FOREVER,
}

_PRESET_MODES = list(_PRESET_TO_HFM.keys())


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one ClimateEntity per FCL485 device."""
    coordinator: ButlerTouchCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ButlerTouchClimate(coordinator, uid)
        for uid in coordinator.data.devices
    )


# ── Entity ────────────────────────────────────────────────────────────────────

class ButlerTouchClimate(CoordinatorEntity[ButlerTouchCoordinator], ClimateEntity):
    """Climate entity for a single FCL485 fancoil unit."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_fan_modes = _FAN_MODES
    _attr_preset_modes = _PRESET_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator: ButlerTouchCoordinator, device_uid: str) -> None:
        super().__init__(coordinator)
        self._device_uid = device_uid
        self._attr_unique_id = f"{DOMAIN}_{device_uid}"

    # ── Data accessors ────────────────────────────────────────────────────────

    @property
    def _dev(self) -> dict[str, Any]:
        return self.coordinator.data.devices[self._device_uid]

    @property
    def _home_uid(self) -> str:
        return self.coordinator.data.home_meta.get("uid", "")

    @property
    def _home_mode(self) -> int:
        # Prefer device-level mode (propagated from home), fall back to home_meta
        return self._dev.get("mode", self.coordinator.data.home_meta.get("mode", HOME_MODE_HEATING))

    @property
    def _is_on(self) -> bool:
        """True when the device is active (not in standby)."""
        return self._dev.get("standBy", {}).get("value", STANDBY_IDLE) == STANDBY_ACTIVE

    # ── HA entity metadata ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._dev.get("name", self._device_uid)

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._dev
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_uid)},
            name=dev.get("name", self._device_uid),
            manufacturer="Innova",
            model=dev.get("type", "FCL485"),
            sw_version=dev.get("firmwareUid"),
        )

    # ── Climate state ─────────────────────────────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        return self._dev.get("tempRoom")

    @property
    def target_temperature(self) -> float | None:
        return self._dev.get("tempSet")

    @property
    def min_temp(self) -> float:
        return self._dev.get("min", 5.0)

    @property
    def max_temp(self) -> float:
        return self._dev.get("max", 40.0)

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._is_on:
            return HVACMode.OFF
        return _HOME_MODE_TO_HVAC.get(self._home_mode, HVACMode.HEAT)

    @property
    def fan_mode(self) -> str:
        fn_value = int(
            self._dev.get("settings", {})
            .get("function", {})
            .get("value", FAN_FUNCTION_AUTO)
        )
        return FAN_FUNCTION_TO_NAME.get(fn_value, "auto")

    @property
    def preset_mode(self) -> str:
        hfm_type = self._dev.get("hfm", {}).get("type", HFM_TYPE_NONE)
        return _HFM_TYPE_TO_PRESET.get(hfm_type, PRESET_NONE)

    # ── Control methods ───────────────────────────────────────────────────────

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature: float | None = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self.coordinator.api.set_setpoint(self._device_uid, temperature)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_temperature failed for %s: %s", self.name, exc)
            return
        # Optimistic update
        self._dev["tempSet"] = temperature
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed (auto / notte / minimo / massimo)."""
        fn_value = FAN_NAME_TO_FUNCTION.get(fan_mode)
        if fn_value is None:
            _LOGGER.warning("Unknown fan mode: %s", fan_mode)
            return
        try:
            await self.coordinator.api.set_function(self._device_uid, fn_value)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_fan_mode failed for %s: %s", self.name, exc)
            return
        # Optimistic update
        self._dev.setdefault("settings", {}).setdefault("function", {})["value"] = str(fn_value)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch between off / heat / cool.

        Turning off:  setHfm(forever) + powerOffDevice
        Turning on:   setHfm(forever) + powerOnDevice + (optionally) setModeHome
        """
        try:
            if hvac_mode == HVACMode.OFF:
                # Keep device off indefinitely until user turns it on again
                await self.coordinator.api.power_off_with_hfm(
                    self._device_uid, HFM_TYPE_FOREVER, -1
                )
                self._dev.setdefault("standBy", {})["value"] = STANDBY_IDLE
                self._dev.setdefault("hfm", {}).update({"type": HFM_TYPE_FOREVER, "deadline": -1})

            else:
                target_mode = _HVAC_TO_HOME_MODE[hvac_mode]

                # Change home-wide mode first if needed
                if target_mode != self._home_mode:
                    await self.coordinator.api.set_home_mode(self._home_uid, target_mode)
                    # Propagate to all devices in coordinator cache
                    self.coordinator.data.home_meta["mode"] = target_mode
                    for dev in self.coordinator.data.devices.values():
                        dev["mode"] = target_mode

                # Turn device on (hold forever so it stays on past schedule)
                await self.coordinator.api.power_on_with_hfm(
                    self._device_uid, HFM_TYPE_FOREVER, -1
                )
                self._dev.setdefault("standBy", {})["value"] = STANDBY_ACTIVE
                self._dev.setdefault("hfm", {}).update({"type": HFM_TYPE_FOREVER, "deadline": -1})

        except ButlerTouchApiError as exc:
            _LOGGER.error("set_hvac_mode failed for %s: %s", self.name, exc)
            return

        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the HFM schedule-override preset.

        none     → return to calendar (device follows its normal schedule)
        1h       → stay in current state for 1 hour
        1d       → stay in current state for 1 day
        forever  → hold indefinitely (never return to schedule)
        """
        hfm_args = _PRESET_TO_HFM.get(preset_mode)
        if hfm_args is None:
            _LOGGER.warning("Unknown preset mode: %s", preset_mode)
            return
        hfm_type, hfm_value = hfm_args
        try:
            await self.coordinator.api.set_hfm(self._device_uid, hfm_type, hfm_value)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_preset_mode failed for %s: %s", self.name, exc)
            return
        # Optimistic update
        self._dev.setdefault("hfm", {}).update(
            {"type": hfm_type, "deadline": hfm_value}
        )
        self.async_write_ha_state()

    # ── Extra attributes ──────────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self._dev
        hfm = dev.get("hfm", {})
        return {
            "device_uid": self._device_uid,
            "device_type": dev.get("type"),
            "address": dev.get("address"),
            "room": dev.get("_room_name"),
            "home_mode": self._home_mode,
            "hfm_type": hfm.get("type"),
            "hfm_deadline": hfm.get("deadline"),
            "hfm_enable": dev.get("hfmEnable"),
            "connection_status": dev.get("connectionStatus", {}).get("status"),
            "offset_heating": dev.get("offsetHeating"),
            "offset_cooling": dev.get("offsetCooling"),
        }
