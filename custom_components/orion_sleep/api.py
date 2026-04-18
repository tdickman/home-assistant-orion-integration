"""Async API client for Orion Sleep."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Callable

import aiohttp

from .const import API_BASE_URL

_LOGGER = logging.getLogger(__name__)


class OrionApiError(Exception):
    """General API error."""


class OrionAuthError(OrionApiError):
    """Authentication failure (401 / invalid tokens)."""


class OrionConnectionError(OrionApiError):
    """Network / connection error."""


class OrionApiClient:
    """Async API client for Orion Sleep."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float = 0,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = expires_at
        self._token_refresh_callback: Callable[[str, str, float], None] | None = None

    def set_token_refresh_callback(
        self, callback: Callable[[str, str, float], None]
    ) -> None:
        """Register callback invoked when tokens are refreshed."""
        self._token_refresh_callback = callback

    # ── Internal helpers ──────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return f"{API_BASE_URL}{path}"

    def _headers(self, with_auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if with_auth and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        with_auth: bool = True,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        """Make an HTTP request and return parsed JSON."""
        url = self._url(path)
        headers = self._headers(with_auth=with_auth)

        try:
            async with self._session.request(
                method, url, headers=headers, json=json_data, params=params
            ) as resp:
                if resp.status == 401:
                    body = await resp.text()
                    raise OrionAuthError(f"Authentication failed: {resp.status} {body}")
                if not resp.ok:
                    body = await resp.text()
                    raise OrionApiError(
                        f"API error on {method} {path}: {resp.status} {resp.reason} - {body}"
                    )
                if resp.content_length == 0:
                    return {}
                return await resp.json()
        except aiohttp.ClientError as err:
            raise OrionConnectionError(
                f"Connection error for {method} {path}: {err}"
            ) from err

    # ── Auth methods (used by config_flow, no bearer token needed) ────

    async def request_auth_code(
        self, email: str | None = None, phone: str | None = None
    ) -> bool:
        """POST /v1/auth/code — send a verification code."""
        body: dict[str, str] = {}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone

        data = await self._request(
            "POST", "/v1/auth/code", with_auth=False, json_data=body
        )
        return data.get("success", False)

    async def verify_auth_code(
        self,
        code: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict:
        """POST /v1/auth/verify — returns session dict with tokens.

        Returns: {"access_token": ..., "refresh_token": ..., "expires_at": ...}
        """
        body: dict[str, str] = {"code": code}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone

        data = await self._request(
            "POST", "/v1/auth/verify", with_auth=False, json_data=body
        )

        # Extract session from nested response structure
        session = (data.get("response") or {}).get("session")
        if not session or "access_token" not in session:
            raise OrionAuthError(f"Unexpected verify response shape: {data}")

        return {
            "access_token": session["access_token"],
            "refresh_token": session["refresh_token"],
            "expires_at": session.get("expires_at", 0),
        }

    # ── Token management ──────────────────────────────────────────────

    def _token_expired(self, margin_seconds: int = 60) -> bool:
        """Return True if the access token is expired or about to expire."""
        return time.time() + margin_seconds >= self._expires_at

    async def ensure_valid_token(self) -> None:
        """Refresh the access token if it is expired or about to expire."""
        if not self._token_expired():
            return
        await self._refresh_tokens()

    async def _refresh_tokens(self) -> None:
        """POST /v1/auth/refresh — refresh the access token."""
        if not self._refresh_token:
            raise OrionAuthError("No refresh token available")

        data = await self._request(
            "POST",
            "/v1/auth/refresh",
            with_auth=False,
            json_data={"refresh_token": self._refresh_token},
        )

        # Handle both nested (response.session) and top-level response shapes
        session = (data.get("response") or {}).get("session", data)
        if "access_token" not in session:
            raise OrionAuthError(f"Unexpected refresh response shape: {data}")

        self._access_token = session["access_token"]
        self._refresh_token = session["refresh_token"]
        self._expires_at = session.get("expires_at", 0)

        if self._token_refresh_callback:
            self._token_refresh_callback(
                self._access_token, self._refresh_token, self._expires_at
            )

    # ── Data fetchers (all require valid token) ───────────────────────

    async def get_current_user(self) -> dict:
        """GET /v1/auth/me — current user profile.

        Returns: {"id": ..., "email": ..., "name": ..., ...}
        (unwrapped from response.response)
        """
        await self.ensure_valid_token()
        data = await self._request("GET", "/v1/auth/me")
        # Real shape: {"response": {user fields}, "success": true}
        return data.get("response", data)

    async def list_devices(self) -> list[dict]:
        """GET /v1/devices — list user's Orion devices.

        Real shape: {"response": {"devices": [...], "shared_with": [...]}, "success": true}
        Each device has: id, serial_number, name, model, type, capabilities,
        temperature_range, temperature_scale, zones, orientation, timezone,
        permissions, default_zone_id, shared_with
        """
        await self.ensure_valid_token()
        data = await self._request("GET", "/v1/devices")
        response = data.get("response", data)
        if isinstance(response, dict):
            return response.get("devices", [])
        if isinstance(response, list):
            return response
        return []

    async def get_sleep_schedules(self) -> dict:
        """GET /v1/sleep-schedules — sleep schedule configuration.

        Real shape: {"response": {"schedules": {<user_id>: [...]},
        "today_sleep_schedule": {<user_id>: {...}},
        "recommendations": {<user_id>: [...]}}, "success": true}
        """
        await self.ensure_valid_token()
        data = await self._request("GET", "/v1/sleep-schedules")
        return data.get("response", data)

    async def get_insights(self, days: int = 7) -> dict:
        """GET /v2/insights — sleep insights for date range.

        Real shape: {"user_id": "...", "data": {"YYYY-MM-DD": {date, score,
        sessions: [{session_id, zone_id, is_in_progress, sleep_summary,
        heart_rate, breath_rate, hrv, temperature, movement, ...}]}},
        "overview": {"YYYY-MM-DD": {"score": N}}}
        Note: NOT wrapped in "response" key.
        """
        await self.ensure_valid_token()
        today = date.today()
        params = {
            "from": (today - timedelta(days=days)).isoformat(),
            "to": today.isoformat(),
        }
        return await self._request("GET", "/v2/insights", params=params)

    # ── Actions ───────────────────────────────────────────────────────

    async def set_temperature(
        self, device_id: str, temperature: float, zone_id: str | None = None
    ) -> dict:
        """PUT /v1/sleep-configurations/temperature — set target temperature.

        NOTE: The exact request body format for this endpoint has not been
        verified against the live API. The OpenAPI spec suggests deviceId +
        temperature + side, but the real API may differ.
        """
        await self.ensure_valid_token()
        body: dict[str, Any] = {
            "deviceId": device_id,
            "temperature": temperature,
        }
        if zone_id:
            body["zone_id"] = zone_id
        return await self._request(
            "PUT", "/v1/sleep-configurations/temperature", json_data=body
        )

    async def set_user_away(self, user_id: str, is_away: bool) -> dict:
        """POST /v1/sleep-configurations/user-away — toggle away/presence.

        is_away=True marks the user as away (presence override that also
        powers the mattress down); is_away=False marks present.

        NOTE: For direct power control, prefer `update_live_device_zones`
        (PUT /v1/devices/{id}/live) — it's the canonical power primitive
        per the OpenAPI spec. `set_user_away` is a presence/schedule
        override that happens to power the device down.

        The response returns the updated device list. When away, zones
        lose their user assignment; when present, users are re-assigned.
        """
        await self.ensure_valid_token()
        return await self._request(
            "POST",
            "/v1/sleep-configurations/user-away",
            json_data={"user_id": user_id, "is_away": is_away},
        )

    # ── Device live / metadata / action endpoints ─────────────────────

    async def update_device(self, device_id: str, **fields: Any) -> dict:
        """PUT /v1/devices/{deviceId} — update device metadata.

        Accepts any subset of: name, orientation ("left"/"right"),
        timezone (IANA). Does NOT control power or temperature.
        """
        await self.ensure_valid_token()
        return await self._request("PUT", f"/v1/devices/{device_id}", json_data=fields)

    async def update_live_device_zones(self, device_id: str, zones: list[dict]) -> dict:
        """PUT /v1/devices/{deviceId}/live — bulk update zone power/temp.

        This is the canonical power control endpoint. Each zone dict must
        include `id` and at least one of `on` (bool) or `temp` (float,
        Celsius for OSCT001-1).

        Example:
            zones=[{"id": "zone_a", "on": True, "temp": 20.5},
                   {"id": "zone_b", "on": False}]
        """
        await self.ensure_valid_token()
        return await self._request(
            "PUT",
            f"/v1/devices/{device_id}/live",
            json_data={"zones": zones},
        )

    async def update_live_device_zone(
        self,
        device_id: str,
        zone_id: str,
        *,
        on: bool | None = None,
        temp: float | None = None,
    ) -> dict:
        """PUT /v1/devices/{deviceId}/live/zones/{zoneId} — single-zone update.

        At least one of `on` or `temp` must be provided. `temp` is in the
        device's native unit (Celsius for OSCT001-1).
        """
        await self.ensure_valid_token()
        body: dict[str, Any] = {}
        if on is not None:
            body["on"] = on
        if temp is not None:
            body["temp"] = temp
        if not body:
            raise ValueError("update_live_device_zone requires `on` or `temp`")
        return await self._request(
            "PUT",
            f"/v1/devices/{device_id}/live/zones/{zone_id}",
            json_data=body,
        )

    async def device_action(
        self, device_id: str, action: str, value: Any | None = None
    ) -> dict:
        """POST /v1/devices/{deviceId}/action — perform device action.

        Not a power endpoint. Valid actions (per DeviceAllowedAction enum):
        split, swap, device_name, device_orientation, device_led_brightness,
        device_quiet_mode, device_reboot, device_reset, device_forget_wifi,
        device_deactivate, invite_user, add_new_guest, remove_guest.
        """
        await self.ensure_valid_token()
        body: dict[str, Any] = {"action": action}
        if value is not None:
            body["value"] = value
        return await self._request(
            "POST", f"/v1/devices/{device_id}/action", json_data=body
        )

    async def activate_device(self, device_id: str, model: str) -> dict:
        """POST /v1/devices/{deviceId}/activate — pair/register a device."""
        await self.ensure_valid_token()
        return await self._request(
            "POST",
            f"/v1/devices/{device_id}/activate",
            json_data={"model": model},
        )

    async def deactivate_device(self, device_id: str) -> dict:
        """POST /v1/devices/{deviceId}/deactivate — unpair a device."""
        await self.ensure_valid_token()
        return await self._request("POST", f"/v1/devices/{device_id}/deactivate")

    async def trigger_firmware_update(self, device_id: str) -> dict:
        """POST /v1/devices/{deviceId}/update — trigger firmware update."""
        await self.ensure_valid_token()
        return await self._request("POST", f"/v1/devices/{device_id}/update")

    async def update_schedule_temperature(
        self, day: int, field: str, celsius: float
    ) -> dict:
        """Update a single temperature field on a specific schedule day.

        PUT /v1/sleep-schedules with body {"schedules": [{"day": N, field: value}]}.
        Verified against live API — partial updates work (only the specified
        field is changed, other fields are preserved).

        Args:
            day: Day of week (0=Monday ... 6=Sunday).
            field: One of bedtime_temp, phase_1_temp, phase_2_temp, wakeup_temp.
            celsius: Absolute Celsius value.
        """
        await self.ensure_valid_token()
        return await self._request(
            "PUT",
            "/v1/sleep-schedules",
            json_data={"schedules": [{"day": day, field: celsius}]},
        )

    async def update_sleep_schedule(
        self, schedule_data: dict, action: str | None = None
    ) -> dict:
        """PUT /v1/sleep-schedules — update sleep schedule."""
        await self.ensure_valid_token()
        params = {}
        if action:
            params["action"] = action
        return await self._request(
            "PUT", "/v1/sleep-schedules", json_data=schedule_data, params=params
        )
