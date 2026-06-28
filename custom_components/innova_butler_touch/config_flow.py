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

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


class ButlerTouchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Innova Butler Touch."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            session = async_get_clientsession(self.hass)
            api = ButlerTouchApi(host=host, port=port, session=session)
            try:
                home = await api.get_homepage()
            except ButlerTouchApiError as exc:
                _LOGGER.debug("Butler Touch connection test failed: %s", exc)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"butler_touch_{host}_{port}")
                self._abort_if_unique_id_configured()
                home_name = home.get("name", host)
                return self.async_create_entry(
                    title=f"Butler Touch – {home_name}",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> ButlerTouchOptionsFlow:
        return ButlerTouchOptionsFlow(entry)


class ButlerTouchOptionsFlow(config_entries.OptionsFlow):
    """Options flow: let user change polling interval."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("scan_interval", default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=3600)
                    )
                }
            ),
        )
