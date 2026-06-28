"""Local HTTP API client for Innova Butler Touch.

The Butler Touch exposes a PHP-based REST API at:
  http://<host>/installedplugin/com.innova.ambiente/2.0/server/index.php

All responses share a common envelope:
  {
    "sw": {"V": "3.x.x"},
    "success": true,
    "errors": {},
    "lb": {},
    "RESULT": { ... }
  }

Key endpoints (all GET unless noted):
  ?Action=getHomepage
      Returns full home structure with all rooms and devices.

  ?Action=detailDevice&deviceUid=<uid>
      Returns a single device's current state.

  ?Action=setSetPoint   [POST]
      Body (form-encoded): deviceUid=<uid>&value=<float>
      Sets the target temperature.

  ?Action=setHfm        [POST]
      Body (form-encoded): deviceUid=<uid>&type=<none|hour|forever>&value=<int>
      Controls the "HFM" (hold/force-mode) standby override:
        type=none  → clear override (restore normal schedule)
        type=hour  → override for <value> hours
        type=forever → hold indefinitely (standby off permanently)
      Effectively used to turn the device on (type=forever) or
      return it to its calendar schedule (type=none).

  ?Action=setModeHome   [POST]
      Body (form-encoded): mode=<int>
      Sets the home heating/cooling mode (1 = heating, 2 = cooling, …).
      This is a home-wide setting, not per-device.

Device state fields (from detailDevice / getHomepage):
  tempRoom    float   Current room temperature (°C)
  tempSet     float   Target setpoint (°C)
  standBy     {value: 0|1}   0 = active, 1 = in standby
  mode        int     Home mode: 1 = heating, 2 = cooling, …
  settings.function.value  int  Fan speed:
                          1 = AUTO, 2 = NIGHT (low), 3 = MIN, 4 = MAX
  min / max   float   Allowed setpoint range
  connectionStatus.status  int  1 = connected, 0 = offline
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_PATH = "/installedplugin/com.innova.ambiente/2.0/server/index.php"

# Home mode values observed in the API
HOME_MODE_HEATING = 1
HOME_MODE_COOLING = 2

# Fan function values
FAN_AUTO = 1
FAN_NIGHT = 2   # low / silent
FAN_MIN = 3
FAN_MAX = 4

# Standby values
STANDBY_OFF = 0  # device is active
STANDBY_ON = 1   # device is in standby


class ButlerTouchApiError(Exception):
    """Raised when the Butler Touch API returns an error or is unreachable."""


class ButlerTouchApi:
    """Async HTTP client for the Innova Butler Touch local API."""

    def __init__(
        self,
        host: str,
        port: int = 80,
        session: aiohttp.ClientSession | None = None,
        request_timeout: int = 10,
    ) -> None:
        self._host = host
        self._port = port
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._owns_session = session is None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self) -> str:
        return f"http://{self._host}:{self._port}{BASE_PATH}"

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.get(
                self._url(), params=params, timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ButlerTouchApiError(f"GET {params} failed: {exc}") from exc

        if not data.get("success", False):
            raise ButlerTouchApiError(
                f"API returned success=false for GET {params}: {data.get('errors')}"
            )
        return data["RESULT"]

    async def _post(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        params = {"Action": action}
        try:
            async with session.post(
                self._url(),
                params=params,
                data=data,
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                result = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ButlerTouchApiError(f"POST {action} failed: {exc}") from exc

        if not result.get("success", False):
            raise ButlerTouchApiError(
                f"API returned success=false for POST {action}: {result.get('errors')}"
            )
        return result.get("RESULT", {})

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_homepage(self) -> dict[str, Any]:
        """Return the full home structure (all rooms and devices).

        The result is the home dict:
        {
          "uid": "...",
          "name": "...",
          "mode": 1,
          "rooms": [
            {
              "uid": "...",
              "name": "...",
              "devices": {
                "<device_uid>": { <device_state> }
              }
            }
          ]
        }

        Note: getHomepage returns `RESULT.user.homes[0]` in the full payload.
        We unwrap to the first home for simplicity (single-home assumption,
        which matches typical Butler Touch deployments).
        """
        result = await self._get({"Action": "getHomepage"})
        # getHomepage wraps in user.homes[]
        if "user" in result:
            homes = result["user"].get("homes", [])
            if not homes:
                raise ButlerTouchApiError("No homes found in getHomepage response")
            return homes[0]
        # Fallback: some firmware versions return the home directly
        return result

    async def get_all_devices(self) -> dict[str, dict[str, Any]]:
        """Return a flat dict of {device_uid: device_state} for all devices."""
        home = await self.get_homepage()
        devices: dict[str, dict[str, Any]] = {}
        for room in home.get("rooms", []):
            for uid, dev in room.get("devices", {}).items():
                dev["_room_name"] = room.get("name", "")
                devices[uid] = dev
        return devices

    async def get_device(self, device_uid: str) -> dict[str, Any]:
        """Return the current state of a single device."""
        return await self._get(
            {"Action": "detailDevice", "deviceUid": device_uid}
        )

    async def set_setpoint(self, device_uid: str, temperature: float) -> None:
        """Set the target temperature for a device (°C)."""
        _LOGGER.debug("set_setpoint %s -> %.1f", device_uid, temperature)
        await self._post(
            "setSetPoint",
            {"deviceUid": device_uid, "value": f"{temperature:.1f}"},
        )

    async def set_standby(self, device_uid: str, standby: bool) -> None:
        """Turn a device on (standby=False) or off (standby=True).

        The Butler Touch does not have a direct on/off command; instead it
        uses the HFM (hold-force-mode) mechanism:
          - standby=False → type=forever (force active indefinitely)
          - standby=True  → type=none    (return to schedule / standby)
        """
        _LOGGER.debug("set_standby %s -> %s", device_uid, standby)
        if standby:
            # Return to calendar schedule (which may put it in standby)
            hfm_type = "none"
            hfm_value = 0
        else:
            # Force device to stay on indefinitely
            hfm_type = "forever"
            hfm_value = -1
        await self._post(
            "setHfm",
            {"deviceUid": device_uid, "type": hfm_type, "value": hfm_value},
        )

    async def set_fan_function(self, device_uid: str, function_value: int) -> None:
        """Set the fan speed / function for a device.

        function_value: 1=AUTO, 2=NIGHT, 3=MIN, 4=MAX
        Note: There is no dedicated setFunction endpoint in the observed API.
        Fan speed is part of the calendar/settings — this is a limitation of
        the local API. This method is included for completeness but may not
        be supported on all firmware versions.
        """
        _LOGGER.warning(
            "set_fan_function is not directly supported by the Butler Touch "
            "local API (no setFunction endpoint observed). Skipping."
        )

    async def set_home_mode(self, mode: int) -> None:
        """Set the home-wide heating/cooling mode.

        mode: 1=heating, 2=cooling (observed values)
        This affects all devices in the home.
        """
        _LOGGER.debug("set_home_mode -> %d", mode)
        await self._post("setModeHome", {"mode": mode})

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        """Return True if the Butler Touch is reachable and responsive."""
        try:
            await self.get_homepage()
            return True
        except ButlerTouchApiError:
            return False
