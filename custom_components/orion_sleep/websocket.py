"""Live device WebSocket client for Orion Sleep.

Connects to ``wss://live.api1.orionbed.com/device/<serial_number>?token=<JWT>``
and streams ``live_device.snapshot`` / ``live_device.update`` frames.

The protocol is validated on-wire by ``orion_info.py --ws-scenario``.
See ``openapi.yaml`` (``/device/{serial_number}`` path and ``x-websocket``
block) and ``AGENTS.md`` (``WebSocket — Live Device Data``) for the
full event taxonomy.

This module implements one WS connection per device, with:
  * ALPN forced to ``http/1.1`` (Cloudflare would otherwise negotiate h2
    and reject the Upgrade).
  * okhttp User-Agent (matches the Android app — some Cloudflare rules
    block generic Python UAs).
  * JWT appended as a ``token`` query parameter.
  * Automatic reconnect with exponential backoff.
  * Automatic token refresh via the shared :class:`.OrionApiClient` when
    the server rejects the Upgrade with 401.
  * Clean close with code 1001 on shutdown, matching the Android app.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import aiohttp

from .api import OrionApiClient, OrionAuthError
from .const import (
    WS_BASE_URL,
    WS_RECONNECT_MAX_DELAY,
    WS_RECONNECT_MIN_DELAY,
    WS_STALE_AFTER_SECONDS,
    WS_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Message handler signature: (serial, event_type, payload_dict).
# event_type is e.g. "live_device.snapshot".
MessageHandler = Callable[[str, str, dict[str, Any]], None]

# State handler signature: (serial, state_string). The state value is
# one of the :class:`OrionWsState` class-level constants.
StateHandler = Callable[[str, str], None]


class OrionWsState:
    """Connection state values surfaced to the coordinator.

    Mirrors the ``connectionState`` enum observed in the Android app.
    """

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DEVICE_OFFLINE = "device_offline"
    AUTH_FAILED = "auth_failed"
    STOPPED = "stopped"


def _build_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that forces HTTP/1.1 via ALPN.

    Cloudflare negotiates HTTP/2 by default which breaks the WS Upgrade
    handshake (RFC 6455 requires HTTP/1.1). Confirmed on-wire.
    """
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


class OrionWebSocketClient:
    """Manage a single live-device WebSocket connection.

    One instance per device serial_number. The connection runs as a
    background task that reconnects indefinitely until :meth:`async_stop`
    is called.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_client: OrionApiClient,
        serial_number: str,
        on_message: MessageHandler,
        on_state_change: StateHandler | None = None,
    ) -> None:
        self._session = session
        self._api_client = api_client
        self._serial = serial_number
        self._on_message = on_message
        self._on_state_change = on_state_change

        self._ssl_ctx = _build_ssl_context()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._state = OrionWsState.STOPPED
        self._last_message_at: float = 0.0
        # Exponential backoff between reconnect attempts.
        self._backoff = WS_RECONNECT_MIN_DELAY

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def serial_number(self) -> str:
        return self._serial

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_message_at(self) -> float:
        """Monotonic time of the most recent frame, or 0 if never."""
        return self._last_message_at

    @property
    def is_fresh(self) -> bool:
        """True if we've seen a frame within :data:`WS_STALE_AFTER_SECONDS`."""
        if not self._last_message_at:
            return False
        return (time.monotonic() - self._last_message_at) <= WS_STALE_AFTER_SECONDS

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background connect/receive loop (idempotent)."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name=f"orion_ws[{self._serial}]")

    async def async_stop(self) -> None:
        """Stop the background loop and close the socket cleanly."""
        self._stop_event.set()
        ws = self._ws
        if ws is not None and not ws.closed:
            try:
                await ws.close(code=1001, message=b"client shutdown")
            except Exception:  # noqa: BLE001 - best effort
                pass
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_state(OrionWsState.STOPPED)

    # ── Internals ──────────────────────────────────────────────────────

    def _set_state(self, new_state: str) -> None:
        if new_state == self._state:
            return
        self._state = new_state
        if self._on_state_change is not None:
            try:
                self._on_state_change(self._serial, new_state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Orion WS state handler raised for %s", self._serial)

    def _ws_url(self, token: str) -> str:
        # The token is URL-quoted so a rogue '&'/'=' can't break the query
        # parsing on the server; the serial is quoted defensively too.
        return (
            f"{WS_BASE_URL}/device/{quote(self._serial, safe='')}"
            f"?token={quote(token, safe='')}"
        )

    async def _run(self) -> None:
        """Outer reconnect loop."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_receive()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - we want to retry
                _LOGGER.debug("Orion WS loop error for %s: %s", self._serial, err)

            if self._stop_event.is_set():
                break

            # Back off before reconnecting.
            self._set_state(OrionWsState.RECONNECTING)
            delay = self._backoff
            self._backoff = min(self._backoff * 2, WS_RECONNECT_MAX_DELAY)
            _LOGGER.debug("Orion WS reconnecting to %s in %.1fs", self._serial, delay)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                # stop_event was set during the wait -> exit.
                break
            except asyncio.TimeoutError:
                # Normal path: backoff elapsed, try again.
                pass

        self._set_state(OrionWsState.STOPPED)

    async def _connect_and_receive(self) -> None:
        """One connection attempt + receive loop."""
        self._set_state(OrionWsState.CONNECTING)

        # Make sure we have a fresh, non-expired JWT before connecting.
        try:
            await self._api_client.ensure_valid_token()
        except OrionAuthError as err:
            _LOGGER.warning("Orion WS cannot connect for %s: %s", self._serial, err)
            self._set_state(OrionWsState.AUTH_FAILED)
            return

        token = self._api_client._access_token  # noqa: SLF001
        if not token:
            self._set_state(OrionWsState.AUTH_FAILED)
            return

        url = self._ws_url(token)
        headers = {"User-Agent": WS_USER_AGENT}

        _LOGGER.debug("Orion WS connecting to /device/%s", self._serial)

        try:
            ws = await self._session.ws_connect(
                url,
                ssl=self._ssl_ctx,
                headers=headers,
                # We don't need aiohttp's own heartbeat — the server pushes
                # a live_device.update roughly every 2s which is a much
                # stronger liveness signal than a protocol ping.
                heartbeat=None,
                compress=0,
            )
        except aiohttp.WSServerHandshakeError as err:
            # 401 -> token probably expired mid-flight. Force a refresh and
            # let the outer loop reconnect on the next iteration.
            if err.status == 401:
                _LOGGER.debug(
                    "Orion WS 401 on /device/%s; refreshing token",
                    self._serial,
                )
                try:
                    await self._api_client._refresh_tokens()  # noqa: SLF001
                except OrionAuthError:
                    self._set_state(OrionWsState.AUTH_FAILED)
                    return
                # Short-circuit the backoff for a 401 — we just refreshed.
                self._backoff = WS_RECONNECT_MIN_DELAY
                return
            if err.status == 404:
                _LOGGER.warning(
                    "Orion WS /device/%s returned 404 (unknown serial)",
                    self._serial,
                )
            else:
                _LOGGER.debug(
                    "Orion WS handshake failed for %s: %s %s",
                    self._serial,
                    err.status,
                    err.message,
                )
            return
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.debug("Orion WS connect failed for %s: %s", self._serial, err)
            return

        self._ws = ws
        self._set_state(OrionWsState.CONNECTED)
        self._backoff = WS_RECONNECT_MIN_DELAY
        self._last_message_at = time.monotonic()

        try:
            async for msg in ws:
                if self._stop_event.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Not observed in captures — log and ignore.
                    _LOGGER.debug(
                        "Orion WS %s: unexpected binary frame (%d bytes)",
                        self._serial,
                        len(msg.data),
                    )
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.debug(
                        "Orion WS %s: error frame: %s",
                        self._serial,
                        ws.exception(),
                    )
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break
        finally:
            self._ws = None
            if not ws.closed:
                try:
                    await ws.close(code=1001, message=b"client shutdown")
                except Exception:  # noqa: BLE001
                    pass

    def _handle_text(self, data: str) -> None:
        """Parse and dispatch one text frame."""
        self._last_message_at = time.monotonic()
        try:
            import json

            parsed = json.loads(data)
        except ValueError:
            _LOGGER.debug(
                "Orion WS %s: non-JSON text frame: %r", self._serial, data[:200]
            )
            return

        if not isinstance(parsed, dict):
            _LOGGER.debug(
                "Orion WS %s: non-object JSON frame: %r",
                self._serial,
                parsed,
            )
            return

        msg_type = parsed.get("type")
        payload = parsed.get("payload")
        if not isinstance(msg_type, str) or not isinstance(payload, dict):
            _LOGGER.debug(
                "Orion WS %s: unexpected frame shape: keys=%s",
                self._serial,
                list(parsed.keys()),
            )
            return

        # Mark device offline / online based on status.online when present.
        status = payload.get("status")
        if isinstance(status, dict) and status.get("online") is False:
            self._set_state(OrionWsState.DEVICE_OFFLINE)
        elif self._state != OrionWsState.CONNECTED:
            # We're receiving frames again — consider the connection healthy.
            self._set_state(OrionWsState.CONNECTED)

        try:
            self._on_message(self._serial, msg_type, payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Orion WS %s: message handler raised for type=%r",
                self._serial,
                msg_type,
            )


async def _stop_all(clients: list[OrionWebSocketClient]) -> None:
    await asyncio.gather(*(c.async_stop() for c in clients), return_exceptions=True)


class OrionWebSocketManager:
    """Manage a group of per-device WebSocket clients.

    The manager is owned by the coordinator; it spawns/tears down one
    :class:`OrionWebSocketClient` per device serial and forwards parsed
    frames back to the coordinator.

    This abstraction exists so the coordinator doesn't have to track the
    lifetime of every socket individually.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_client: OrionApiClient,
        on_message: MessageHandler,
        on_state_change: StateHandler | None = None,
    ) -> None:
        self._session = session
        self._api_client = api_client
        self._on_message = on_message
        self._on_state_change = on_state_change
        self._clients: dict[str, OrionWebSocketClient] = {}

    def sync_to_serials(self, serials: list[str]) -> None:
        """Start clients for new serials; stop clients for removed ones.

        Safe to call on every coordinator refresh — it's a no-op when
        the set of serials hasn't changed.
        """
        target = set(s for s in serials if s)

        # Stop anything that's no longer wanted.
        to_stop: list[OrionWebSocketClient] = []
        for serial in list(self._clients):
            if serial not in target:
                to_stop.append(self._clients.pop(serial))
        if to_stop:
            # Fire-and-forget — we don't want to block the coordinator.
            asyncio.create_task(_stop_all(to_stop))

        # Start anything new.
        for serial in target:
            if serial in self._clients:
                continue
            client = OrionWebSocketClient(
                self._session,
                self._api_client,
                serial,
                on_message=self._on_message,
                on_state_change=self._on_state_change,
            )
            client.start()
            self._clients[serial] = client

    def state(self, serial: str) -> str:
        client = self._clients.get(serial)
        return client.state if client else OrionWsState.STOPPED

    def is_fresh(self, serial: str) -> bool:
        client = self._clients.get(serial)
        return bool(client and client.is_fresh)

    def last_message_at(self, serial: str) -> float:
        client = self._clients.get(serial)
        return client.last_message_at if client else 0.0

    async def async_stop(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        await _stop_all(clients)
