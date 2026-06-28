"""Climate platform for Innova Butler Touch (FCL485 fancoils).

Each FCL485 fancoil is a ClimateEntity with:

  HVAC modes       off / heat_cool (on)
  ───────────────────────────────────────
  off          → setHfm(forever) + powerOffDevice
  heat_cool    → setHfm(forever) + powerOnDevice
  (heating vs cooling is a home-wide setting, exposed via the separate
   ButlerTouchHomeModeSelect entity — not per fancoil)

  Fan modes        auto · notte · minimo · massimo
  ─────────────────────────────────────────────────
  → setFunction (1–4)
  Values read from settings.function.value (string, converted to int)

  Target temperature
  ──────────────────
  → setSetPoint

  Preset modes (HFM schedule override)
  ──────────────────────────────────────
  none     → setHfm none/0    (follow calendar)
  1h       → setHfm hour/1
  1d       → setHfm day/24
  forever  → setHfm forever/-1
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.select import SelectEntity
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

# ── Fan modes ────────────────────────────────────────────────────────────────

_FAN_MODES = list(FAN_FUNCTION_TO_NAME.values())  # auto · notte · minimo · massimo

# ── Preset modes (HFM) ───────────────────────────────────────────────────────

PRESET_NONE = "none"
PRESET_1H = "1h"
PRESET_1D = "1d"
PRESET_FOREVER = "forever"

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

# ── Home mode select options ─────────────────────────────────────────────────

_HOME_MODE_OPTIONS = ["riscaldamento", "raffreddamento"]
_HOME_MODE_TO_VALUE = {
    "riscaldamento": HOME_MODE_HEATING,   # 0
    "raffreddamento": HOME_MODE_COOLING,  # 1
}
_HOME_MODE_FROM_VALUE = {v: k for k, v in _HOME_MODE_TO_VALUE.items()}


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ButlerTouchCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list = []

    # One climate entity per fancoil
    for uid in coordinator.data.devices:
        entities.append(ButlerTouchClimate(coordinator, uid))

    # One home-wide mode select entity
    entities.append(ButlerTouchHomeModeSelect(coordinator))

    async_add_entities(entities)


# ── Fancoil ClimateEntity ─────────────────────────────────────────────────────

class ButlerTouchClimate(CoordinatorEntity[ButlerTouchCoordinator], ClimateEntity):
    """Climate entity for a single FCL485 fancoil.

    HVAC modes: off / heat_cool (on).
    Heating vs cooling is controlled home-wide via ButlerTouchHomeModeSelect.
    """

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
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

    # ── Data helpers ─────────────────────────────────────────────────────────

    @property
    def _dev(self) -> dict[str, Any]:
        return self.coordinator.data.devices[self._device_uid]

    @property
    def _is_on(self) -> bool:
        return self._dev.get("standBy", {}).get("value", STANDBY_IDLE) == STANDBY_ACTIVE

    # ── Entity metadata ───────────────────────────────────────────────────────

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
        return HVACMode.HEAT_COOL if self._is_on else HVACMode.OFF

    @property
    def fan_mode(self) -> str:
        # value is a string in the API response e.g. "1", "4"
        raw = self._dev.get("settings", {}).get("function", {}).get("value", "1")
        try:
            fn_int = int(raw)
        except (ValueError, TypeError):
            fn_int = FAN_FUNCTION_AUTO
        return FAN_FUNCTION_TO_NAME.get(fn_int, "auto")

    @property
    def preset_mode(self) -> str:
        hfm_type = self._dev.get("hfm", {}).get("type", HFM_TYPE_NONE)
        return _HFM_TYPE_TO_PRESET.get(hfm_type, PRESET_NONE)

    # ── Control ───────────────────────────────────────────────────────────────

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature: float | None = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self.coordinator.api.set_setpoint(self._device_uid, temperature)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_temperature failed for %s: %s", self.name, exc)
            return
        self._dev["tempSet"] = temperature
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        try:
            if hvac_mode == HVACMode.OFF:
                await self.coordinator.api.power_off_with_hfm(
                    self._device_uid, HFM_TYPE_FOREVER, -1
                )
                self._dev.setdefault("standBy", {})["value"] = STANDBY_IDLE
                self._dev.setdefault("hfm", {}).update(
                    {"type": HFM_TYPE_FOREVER, "deadline": -1}
                )
            else:  # HEAT_COOL → turn on
                await self.coordinator.api.power_on_with_hfm(
                    self._device_uid, HFM_TYPE_FOREVER, -1
                )
                self._dev.setdefault("standBy", {})["value"] = STANDBY_ACTIVE
                self._dev.setdefault("hfm", {}).update(
                    {"type": HFM_TYPE_FOREVER, "deadline": -1}
                )
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_hvac_mode failed for %s: %s", self.name, exc)
            return
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        fn_value = FAN_NAME_TO_FUNCTION.get(fan_mode)
        if fn_value is None:
            _LOGGER.warning("Unknown fan mode: %s", fan_mode)
            return
        try:
            await self.coordinator.api.set_function(self._device_uid, fn_value)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_fan_mode failed for %s: %s", self.name, exc)
            return
        # value is stored as string in device state
        self._dev.setdefault("settings", {}).setdefault("function", {})["value"] = str(fn_value)
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
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
        self._dev.setdefault("hfm", {}).update({"type": hfm_type, "deadline": hfm_value})
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
            "hfm_type": hfm.get("type"),
            "hfm_deadline": hfm.get("deadline"),
            "connection_status": dev.get("connectionStatus", {}).get("status"),
            "offset_heating": dev.get("offsetHeating"),
            "offset_cooling": dev.get("offsetCooling"),
        }


# ── Home-wide mode SelectEntity ───────────────────────────────────────────────

class ButlerTouchHomeModeSelect(CoordinatorEntity[ButlerTouchCoordinator], SelectEntity):
    """Select entity for the home-wide riscaldamento / raffreddamento switch."""

    _attr_has_entity_name = True
    _attr_name = "Modalità impianto"
    _attr_icon = "mdi:hvac"
    _attr_options = _HOME_MODE_OPTIONS

    def __init__(self, coordinator: ButlerTouchCoordinator) -> None:
        super().__init__(coordinator)
        home_uid = coordinator.data.home_meta.get("uid", "home")
        self._attr_unique_id = f"{DOMAIN}_{home_uid}_home_mode"

    @property
    def device_info(self) -> DeviceInfo:
        meta = self.coordinator.data.home_meta
        return DeviceInfo(
            identifiers={(DOMAIN, meta.get("uid", "home"))},
            name=meta.get("name", "Butler Touch"),
            manufacturer="Innova",
            model="Butler Touch",
        )

    @property
    def current_option(self) -> str:
        mode = self.coordinator.data.home_meta.get("mode", HOME_MODE_COOLING)
        return _HOME_MODE_FROM_VALUE.get(mode, "raffreddamento")

    async def async_select_option(self, option: str) -> None:
        mode_value = _HOME_MODE_TO_VALUE.get(option)
        if mode_value is None:
            return
        home_uid = self.coordinator.data.home_meta.get("uid", "")
        try:
            await self.coordinator.api.set_home_mode(home_uid, mode_value)
        except ButlerTouchApiError as exc:
            _LOGGER.error("set_home_mode failed: %s", exc)
            return
        self.coordinator.data.home_meta["mode"] = mode_value
        self.async_write_ha_state()
