"""Innova Butler Touch Home Assistant integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ButlerTouchApi, ButlerTouchApiError
from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SELECT]


@dataclass
class ButlerTouchData:
    """Combined data snapshot returned by the coordinator."""

    home_meta: dict[str, Any] = field(default_factory=dict)
    """Home-level info: uid, name, mode."""

    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Flat map of device_uid → device state dict."""


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innova Butler Touch from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    session = async_get_clientsession(hass)
    api = ButlerTouchApi(host=host, port=port, session=session)

    coordinator = ButlerTouchCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()

    if not coordinator.data or not coordinator.data.devices:
        raise ConfigEntryNotReady(
            f"Could not retrieve device list from Butler Touch at {host}:{port}"
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class ButlerTouchCoordinator(DataUpdateCoordinator[ButlerTouchData]):
    """Coordinator that polls the Butler Touch for device states."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ButlerTouchApi,
        entry: ConfigEntry,
    ) -> None:
        self.api = api
        self.entry = entry
        scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> ButlerTouchData:
        """Fetch the latest state from the Butler Touch."""
        try:
            home_meta, devices = await self.api.get_all_devices()
        except ButlerTouchApiError as exc:
            raise UpdateFailed(f"Butler Touch update failed: {exc}") from exc
        return ButlerTouchData(home_meta=home_meta, devices=devices)
