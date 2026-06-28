"""Local HTTP API client for Innova Butler Touch.

All requests go to:
  http://<host>:<port>/installedplugin/com.innova.ambiente/2.0/server/index.php

Every response envelope:
  { "sw": {...}, "success": true, "errors": {}, "RESULT": { ... } }

getHomepage RESULT structure (confirmed from flow capture):
  RESULT = {
    "id": "", "accessToken": "...", "name": "Butler", ...,
    "homes": [{
      "mode": 1,          ← home-wide mode: 0=riscaldamento, 1=raffreddamento
      "name": "Casetta",
      "uid": "413a...",
      "uniqueID": "55d9...",
      "rooms": [{
        "uid": "...", "name": "Interrato",
        "devices": {
          "<device_uid>": {
            "uid": "...", "name": "...", "type": "FCL485",
            "mode": 1,          ← mirrors home mode
            "tempRoom": 24.0,
            "tempSet": 19.0,
            "standBy": {"value": 1},   ← 0=active, 1=standby
            "settings": {
              "function": {
                "value": "1",           ← STRING: 1=auto 2=notte 3=minimo 4=massimo
                "fieldOptions": [
                  {"value": 1, "label": "FUNCTION_AUTO"},
                  {"value": 2, "label": "FUNCTION_NIGHT"},
                  {"value": 3, "label": "FUNCTION_MIN"},
                  {"value": 4, "label": "FUNCTION_MAX"}
                ]
              }
            },
            "hfm": {"type": "none", "deadline": 0},
            "min": 5.0, "max": 40.0,
            "hfmEnable": true
          }
        }
      }]
    }]
  }

NOTE: RESULT is the user object directly (no extra "user" wrapper key).
      homes[0] is the first (and typically only) home.
      The home-level "mode" field is the authoritative heating/cooling flag.

Endpoints
─────────
GET  ?Action=getHomepage
GET  ?Action=detailDevice&deviceUid=<uid>
POST ?Action=setSetPoint      body: deviceUid=<uid>&value=<float>
POST ?Action=setFunction      body: function=<1-4>&deviceUid=<uid>
POST ?Action=powerOnDevice    body: deviceUid=<uid>
POST ?Action=powerOffDevice   body: deviceUid=<uid>
POST ?Action=setHfm           body: deviceUid=<uid>&type=<none|hour|day|forever>&value=<int>
POST ?Action=setModeHome      body: mode=<0|1>&homeUid=<uid>
                                     0=riscaldamento (heating), 1=raffreddamento (cooling)
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

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

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

    # ── Homepage / device discovery ───────────────────────────────────────────

    async def get_homepage(self) -> dict[str, Any]:
        """Return homes[0] dict: {uid, name, mode, rooms[]}.

        RESULT is the user object directly (no "user" wrapper key).
        Structure: RESULT.homes[0]
        """
        result = await self._get({"Action": "getHomepage"})
        # RESULT is the user object: {"id":"","homes":[{...}],...}
        homes = result.get("homes", [])
        if not homes:
            raise ButlerTouchApiError("getHomepage returned no homes")
        return homes[0]

    async def get_all_devices(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Return (home_meta, {device_uid: device_state}).

        home_meta: {"uid": str, "name": str, "mode": int}
          mode 0 = riscaldamento (heating)
          mode 1 = raffreddamento (cooling)
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
        return await self._get({"Action": "detailDevice", "deviceUid": device_uid})

    # ── Temperature ───────────────────────────────────────────────────────────

    async def set_setpoint(self, device_uid: str, temperature: float) -> None:
        _LOGGER.debug("set_setpoint %s → %.1f°C", device_uid, temperature)
        await self._post(
            "setSetPoint",
            {"deviceUid": device_uid, "value": f"{temperature:.1f}"},
        )

    # ── Fan speed ─────────────────────────────────────────────────────────────

    async def set_function(self, device_uid: str, function_value: int) -> None:
        """Set fan speed. function_value: 1=auto 2=notte 3=minimo 4=massimo."""
        _LOGGER.debug("set_function %s → %d", device_uid, function_value)
        await self._post(
            "setFunction",
            {"function": function_value, "deviceUid": device_uid},
        )

    # ── Power ─────────────────────────────────────────────────────────────────

    async def power_on(self, device_uid: str) -> None:
        _LOGGER.debug("power_on %s", device_uid)
        await self._post("powerOnDevice", {"deviceUid": device_uid})

    async def power_off(self, device_uid: str) -> None:
        _LOGGER.debug("power_off %s", device_uid)
        await self._post("powerOffDevice", {"deviceUid": device_uid})

    async def power_on_with_hfm(
        self,
        device_uid: str,
        hfm_type: str = "forever",
        hfm_value: int = -1,
    ) -> None:
        """Set HFM override then power on (mirrors app behaviour)."""
        await self.set_hfm(device_uid, hfm_type, hfm_value)
        await self.power_on(device_uid)

    async def power_off_with_hfm(
        self,
        device_uid: str,
        hfm_type: str = "forever",
        hfm_value: int = -1,
    ) -> None:
        """Set HFM override then power off (mirrors app behaviour)."""
        await self.set_hfm(device_uid, hfm_type, hfm_value)
        await self.power_off(device_uid)

    # ── HFM (schedule override) ───────────────────────────────────────────────

    async def set_hfm(self, device_uid: str, hfm_type: str, value: int) -> None:
        """Set schedule override.
        hfm_type/value: "none"/0  "hour"/N  "day"/N  "forever"/-1
        """
        _LOGGER.debug("set_hfm %s type=%s value=%d", device_uid, hfm_type, value)
        await self._post(
            "setHfm",
            {"deviceUid": device_uid, "type": hfm_type, "value": value},
        )

    # ── Home-wide mode ────────────────────────────────────────────────────────

    async def set_home_mode(self, home_uid: str, mode: int) -> None:
        """Set home-wide mode. mode: 0=riscaldamento, 1=raffreddamento."""
        _LOGGER.debug("set_home_mode homeUid=%s mode=%d", home_uid, mode)
        await self._post("setModeHome", {"mode": mode, "homeUid": home_uid})

    # ── Connectivity ──────────────────────────────────────────────────────────

    async def test_connection(self) -> bool:
        try:
            await self.get_homepage()
            return True
        except ButlerTouchApiError:
            return False
