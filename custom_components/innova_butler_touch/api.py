"""Local HTTP API client for Innova Butler Touch.

All requests go to:
  http://<host>:<port>/installedplugin/com.innova.ambiente/2.0/server/index.php

Every response envelope:
  {
    "sw":      { "V": "3.x.x" },
    "success": true,
    "errors":  {},
    "RESULT":  { ... }
  }

Observed endpoints
──────────────────
GET  ?Action=getHomepage
     Returns the full home tree: home → rooms → devices.
     Home state includes homeUid, name, mode (0/1) and all device states.

GET  ?Action=detailDevice&deviceUid=<uid>
     Returns a single device's current state dict.

POST ?Action=setSetPoint
     Body: deviceUid=<uid>&value=<float>
     Sets the target temperature.

POST ?Action=setFunction
     Body: function=<1-4>&deviceUid=<uid>
     Sets fan speed: 1=auto 2=notte 3=minimo 4=massimo.

POST ?Action=powerOnDevice
     Body: deviceUid=<uid>
     Turns a device ON (clears powered-off state, activates it).

POST ?Action=powerOffDevice
     Body: deviceUid=<uid>
     Turns a device OFF completely (not just standby — powered off).

POST ?Action=setHfm
     Body: deviceUid=<uid>&type=<none|hour|day|forever>&value=<int>
     Sets the schedule-override (Hold-Force-Mode):
       type=none    value=0    → clear any override, return to calendar
       type=hour    value=N    → override for N hours (N ≥ 1)
       type=day     value=N    → override for N×24 hours (e.g. value=24 = 1 day)
       type=forever value=-1   → hold indefinitely
     The HFM determines whether the device follows its calendar or stays in
     a user-forced state. It must be set BEFORE powerOn/powerOff or setFunction
     when the user wants a timed or permanent override.

POST ?Action=setModeHome
     Body: mode=<0|1>&homeUid=<uid>
     Sets the home-wide heating/cooling mode:
       0 = heating  (riscaldamento)
       1 = cooling  (raffreddamento)
     Confirmed from JS source getModeOptions():
       { value: 0, label: "MODE_HEATING" }
       { value: 1, label: "MODE_COOLING" }
     Note: this is a home-wide setting — all devices are affected.

Device state fields (from getHomepage / detailDevice)
──────────────────────────────────────────────────────
  uid              str    Device unique identifier
  name             str    Room/device name
  type             str    "FCL485"
  mode             int    Home mode: 0=auto 1=heating
  tempRoom         float  Current room temperature (°C)
  tempSet          float  Target setpoint (°C)
  min              float  Minimum allowed setpoint
  max              float  Maximum allowed setpoint
  standBy.value    int    0=active 1=standby
  settings.function.value  int  Fan speed (1–4)
  hfm.type         str    Current override type (none/hour/day/forever)
  hfm.deadline     int    Unix timestamp of override expiry (0 or -1 = n/a)
  hfmEnable        bool   Whether HFM is supported on this device
  connectionStatus.status  int  1=online 0=offline
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_API_PATH = "/installedplugin/com.innova.ambiente/2.0/server/index.php"


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

    # ── Session management ────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _url(self) -> str:
        return f"http://{self._host}:{self._port}{_API_PATH}"

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
                f"API error for GET {params}: {data.get('errors')}"
            )
        return data["RESULT"]

    async def _post(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.post(
                self._url(),
                params={"Action": action},
                data=body,
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ButlerTouchApiError(f"POST {action} failed: {exc}") from exc
        if not data.get("success", False):
            raise ButlerTouchApiError(
                f"API error for POST {action}: {data.get('errors')}"
            )
        return data.get("RESULT", {})

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_homepage(self) -> dict[str, Any]:
        """Return the home dict (uid, name, mode, rooms[]).

        The API wraps data in user.homes[0]; we unwrap for convenience.
        """
        result = await self._get({"Action": "getHomepage"})
        if "user" in result:
            homes = result["user"].get("homes", [])
            if not homes:
                raise ButlerTouchApiError("getHomepage returned no homes")
            return homes[0]
        return result  # some firmware versions return the home directly

    async def get_all_devices(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Return (home_meta, {device_uid: device_state}) for all devices.

        home_meta contains uid, name, mode (used for setModeHome).
        """
        home = await self.get_homepage()
        home_meta = {
            "uid": home.get("uid", ""),
            "name": home.get("name", ""),
            "mode": home.get("mode", 1),
        }
        devices: dict[str, dict[str, Any]] = {}
        for room in home.get("rooms", []):
            for uid, dev in room.get("devices", {}).items():
                dev["_room_name"] = room.get("name", "")
                devices[uid] = dev
        return home_meta, devices

    async def get_device(self, device_uid: str) -> dict[str, Any]:
        """Return the current state of a single device."""
        return await self._get({"Action": "detailDevice", "deviceUid": device_uid})

    # ── Temperature ───────────────────────────────────────────────────────────

    async def set_setpoint(self, device_uid: str, temperature: float) -> None:
        """Set the target temperature for a device (°C)."""
        _LOGGER.debug("set_setpoint %s → %.1f°C", device_uid, temperature)
        await self._post(
            "setSetPoint",
            {"deviceUid": device_uid, "value": f"{temperature:.1f}"},
        )

    # ── Fan speed ─────────────────────────────────────────────────────────────

    async def set_function(self, device_uid: str, function_value: int) -> None:
        """Set fan speed / function for a device.

        function_value: 1=auto  2=notte  3=minimo  4=massimo
        POST body: function=<value>&deviceUid=<uid>
        """
        _LOGGER.debug("set_function %s → %d", device_uid, function_value)
        await self._post(
            "setFunction",
            {"function": function_value, "deviceUid": device_uid},
        )

    # ── Power on / off ────────────────────────────────────────────────────────

    async def power_on(self, device_uid: str) -> None:
        """Turn a device fully ON.

        POST body: deviceUid=<uid>
        The app typically also sets an HFM override before calling this so
        the device stays on for a defined period. Use power_on_with_hfm()
        for that combined behaviour.
        """
        _LOGGER.debug("power_on %s", device_uid)
        await self._post("powerOnDevice", {"deviceUid": device_uid})

    async def power_off(self, device_uid: str) -> None:
        """Turn a device fully OFF.

        POST body: deviceUid=<uid>
        """
        _LOGGER.debug("power_off %s", device_uid)
        await self._post("powerOffDevice", {"deviceUid": device_uid})

    # ── HFM (schedule override) ───────────────────────────────────────────────

    async def set_hfm(
        self,
        device_uid: str,
        hfm_type: str,
        value: int,
    ) -> None:
        """Set the schedule-override (Hold-Force-Mode).

        hfm_type / value combinations:
          "none"    0    → clear override, return to calendar
          "hour"    N    → override for N hours  (N ≥ 1)
          "day"     N    → override for N×24 h   (e.g. 24 = 1 day)
          "forever" -1   → hold indefinitely
        """
        _LOGGER.debug("set_hfm %s type=%s value=%d", device_uid, hfm_type, value)
        await self._post(
            "setHfm",
            {"deviceUid": device_uid, "type": hfm_type, "value": value},
        )

    async def clear_hfm(self, device_uid: str) -> None:
        """Convenience: clear any active HFM override (return to calendar)."""
        await self.set_hfm(device_uid, "none", 0)

    # ── Combined helpers (matching app behaviour) ─────────────────────────────

    async def power_on_with_hfm(
        self,
        device_uid: str,
        hfm_type: str = "forever",
        hfm_value: int = -1,
    ) -> None:
        """Turn device ON and set an HFM override.

        The Butler Touch app always calls setHfm before powerOnDevice so the
        device knows how long to stay on. Default is 'forever' (indefinite).
        """
        await self.set_hfm(device_uid, hfm_type, hfm_value)
        await self.power_on(device_uid)

    async def power_off_with_hfm(
        self,
        device_uid: str,
        hfm_type: str = "forever",
        hfm_value: int = -1,
    ) -> None:
        """Turn device OFF and optionally set an HFM override.

        Default is 'forever' so the device stays off until explicitly turned on.
        """
        await self.set_hfm(device_uid, hfm_type, hfm_value)
        await self.power_off(device_uid)

    # ── Home-wide mode ────────────────────────────────────────────────────────

    async def set_home_mode(self, home_uid: str, mode: int) -> None:
        """Set the home-wide heating/cooling mode.

        mode: 0=heating (riscaldamento), 1=cooling (raffreddamento)
        Use the HOME_MODE_HEATING / HOME_MODE_COOLING constants from const.py.
        Requires homeUid from getHomepage.
        """
        _LOGGER.debug("set_home_mode homeUid=%s mode=%d", home_uid, mode)
        await self._post("setModeHome", {"mode": mode, "homeUid": home_uid})

    # ── Connectivity ──────────────────────────────────────────────────────────

    async def test_connection(self) -> bool:
        """Return True if the Butler Touch is reachable."""
        try:
            await self.get_homepage()
            return True
        except ButlerTouchApiError:
            return False
