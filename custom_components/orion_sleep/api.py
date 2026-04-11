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
