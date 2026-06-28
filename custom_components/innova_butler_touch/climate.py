"""Climate platform for Innova Butler Touch (FCL485 fancoils)."""

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

from .api import ButlerTouchApiError, HOME_MODE_COOLING, HOME_MODE_HEATING
from .const import (
    DOMAIN,
    FAN_FUNCTION_AUTO,
    FAN_FUNCTION_BY_NAME,
    FAN_FUNCTION_MAX,
    FAN_FUNCTION_MIN,
    FAN_FUNCTION_NAMES,
    FAN_FUNCTION_NIGHT,
    HOME_MODE_COOLING,
    HOME_MODE_HEATING,
)
from . import ButlerTouchCoordinator

_LOGGER = logging.getLogger(__name__)

# Map Butler Touch home mode → HA HVAC mode
# The Butler Touch uses a single home-wide mode (heating or cooling).
# Devices are "off" when standBy.value == 1.
_HOME_MODE_TO_HVAC: dict[int, HVACMode] = {
    HOME_MODE_HEATING: HVACMode.HEAT,
    HOME_MODE_COOLING: HVACMode.COOL,
}
_HVAC_TO_HOME_MODE: dict[HVACMode, int] = {v: k for k, v in _HOME_MODE_TO_HVAC.items()}

# Fan speed labels exposed to HA
_FAN_MODES = ["auto", "night", "min", "max"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator: ButlerTouchCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        ButlerTouchClimate(coordinator, device_uid)
        for device_uid in coordinator.data
    ]
    async_add_entities(entities)


class ButlerTouchClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for a single Innova FCL485 fancoil on the Butler Touch."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_fan_modes = _FAN_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]

    def __init__(self, coordinator: ButlerTouchCoordinator, device_uid: str) -> None:
        super().__init__(coordinator)
        self._device_uid = device_uid
        self._attr_unique_id = f"{DOMAIN}_{device_uid}"

    # ------------------------------------------------------------------
    # Helper to access current device data from coordinator cache
    # ------------------------------------------------------------------

    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data[self._device_uid]

    # ------------------------------------------------------------------
    # HA entity metadata
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._device.get("name", self._device_uid)

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._device
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_uid)},
            name=dev.get("name", self._device_uid),
            manufacturer="Innova",
            model=dev.get("type", "FCL485"),
            sw_version=dev.get("firmwareUid"),
            via_device=(DOMAIN, self.coordinator.entry.entry_id),
        )

    # ------------------------------------------------------------------
    # Climate state properties
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        return self._device.get("tempRoom")

    @property
    def target_temperature(self) -> float | None:
        return self._device.get("tempSet")

    @property
    def min_temp(self) -> float:
        return self._device.get("min", 5.0)

    @property
    def max_temp(self) -> float:
        return self._device.get("max", 40.0)

    @property
    def target_temperature_step(self) -> float:
        return 0.5

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode.

        - standBy.value == 1 → OFF
        - otherwise → map home mode (heating/cooling) to HA mode
        """
        standby = self._device.get("standBy", {}).get("value", 1)
        if standby == 1:
            return HVACMode.OFF

        home_mode = self._device.get("mode", HOME_MODE_HEATING)
        return _HOME_MODE_TO_HVAC.get(home_mode, HVACMode.HEAT)

    @property
    def fan_mode(self) -> str | None:
        """Return current fan mode from settings.function.value."""
        fn_value = int(
            self._device.get("settings", {})
            .get("function", {})
            .get("value", FAN_FUNCTION_AUTO)
        )
        return FAN_FUNCTION_NAMES.get(fn_value, "auto")

    # ------------------------------------------------------------------
    # Climate control methods
    # ------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self.coordinator.api.set_setpoint(self._device_uid, temperature)
        except ButlerTouchApiError as exc:
            _LOGGER.error("Failed to set temperature for %s: %s", self.name, exc)
            return
        # Optimistic update
        self.coordinator.data[self._device_uid]["tempSet"] = temperature
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (off, heat, cool)."""
        try:
            if hvac_mode == HVACMode.OFF:
                # Put device in standby
                await self.coordinator.api.set_standby(self._device_uid, standby=True)
                self.coordinator.data[self._device_uid]["standBy"] = {"value": 1}
            else:
                # Wake device up (force active)
                await self.coordinator.api.set_standby(self._device_uid, standby=False)
                self.coordinator.data[self._device_uid]["standBy"] = {"value": 0}

                # Change home-wide heating/cooling mode if needed
                target_home_mode = _HVAC_TO_HOME_MODE.get(hvac_mode)
                if target_home_mode and target_home_mode != self._device.get("mode"):
                    await self.coordinator.api.set_home_mode(target_home_mode)
                    # Update all devices' mode in coordinator cache
                    for dev in self.coordinator.data.values():
                        dev["mode"] = target_home_mode
        except ButlerTouchApiError as exc:
            _LOGGER.error("Failed to set HVAC mode for %s: %s", self.name, exc)
            return
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed.

        Note: The Butler Touch local API does not expose a setFunction
        endpoint (fan speed is managed via calendar settings). This method
        logs a warning rather than silently failing.
        """
        _LOGGER.warning(
            "Fan mode control ('%s') is not supported by the Butler Touch "
            "local API. The fan speed is managed by the device's calendar "
            "schedule and cannot be changed directly via the local HTTP API.",
            fan_mode,
        )

    # ------------------------------------------------------------------
    # Extra state attributes (useful for debugging / automations)
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self._device
        return {
            "device_uid": self._device_uid,
            "device_type": dev.get("type"),
            "address": dev.get("address"),
            "room": dev.get("_room_name"),
            "connection_status": dev.get("connectionStatus", {}).get("status"),
            "hfm_type": dev.get("hfm", {}).get("type"),
            "offset_heating": dev.get("offsetHeating"),
            "offset_cooling": dev.get("offsetCooling"),
        }
