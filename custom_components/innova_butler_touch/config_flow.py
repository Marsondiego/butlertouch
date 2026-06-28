"""Config flow for Innova Butler Touch."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ButlerTouchApi, ButlerTouchApiError
from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


class ButlerTouchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Innova Butler Touch."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            session = async_get_clientsession(self.hass)
            api = ButlerTouchApi(host=host, port=port, session=session)

            try:
                home = await api.get_homepage()
                home_name = home.get("name", host)
            except ButlerTouchApiError as exc:
                _LOGGER.warning("Butler Touch connection failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"butler_touch_{host}_{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Butler Touch ({home_name})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ButlerTouchOptionsFlow:
        return ButlerTouchOptionsFlow(config_entry)


class ButlerTouchOptionsFlow(config_entries.OptionsFlow):
    """Handle options (polling interval)."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._entry.options.get(
            "scan_interval", DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional("scan_interval", default=current_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=10, max=3600)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
