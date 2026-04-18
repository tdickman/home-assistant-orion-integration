"""DataUpdateCoordinator for Orion Sleep."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OrionApiClient, OrionApiError, OrionAuthError, OrionConnectionError
from .const import (
    CONF_INSIGHTS_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_INSIGHTS_DAYS,
    DEFAULT_SCAN_INTERVAL,
)
from .websocket import OrionWebSocketManager, OrionWsState

_LOGGER = logging.getLogger(__name__)

OrionConfigEntry = ConfigEntry  # ConfigEntry[OrionDataUpdateCoordinator]


class OrionDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Fetch data from Orion API."""

    config_entry: OrionConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: OrionConfigEntry,
        api_client: OrionApiClient,
    ) -> None:
        interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name="orion_sleep",
            config_entry=config_entry,
            update_interval=timedelta(seconds=interval),
        )
        self.api_client = api_client
        self.devices: list[dict] = []
        # Live snapshots keyed by device id (UUID). Populated from
        # GET /v1/devices/{serial}/live on each poll AND from
        # live_device.{snapshot,update} frames on the per-device WebSocket.
        # The WS stream supersedes the polled state between polls, giving
        # realtime zone on/temp + status updates without waiting for the
        # next REST poll. Note that biometric-derived fields like
        # status.sensors.*.status_text (on-bed classification) lag the
        # real event by ~30s–1min because the topper itself is slow to
        # decide; the WS frame arrival is not the bottleneck there.
        self.live_devices: dict[str, dict] = {}
        self.user: dict = {}
        self.user_id: str = ""

        # Maps device serial_number -> UUID so the WS message handler
        # (which only knows the serial) can key into live_devices.
        self._serial_to_id: dict[str, str] = {}

        # Live WebSocket manager — one connection per device serial.
        self._ws_manager: OrionWebSocketManager = OrionWebSocketManager(
            session=async_get_clientsession(hass),
            api_client=api_client,
            on_message=self._handle_ws_message,
            on_state_change=self._handle_ws_state,
        )

    async def _async_setup(self) -> None:
        """Load one-time data: user profile, device list."""
        try:
            self.user = await self.api_client.get_current_user()
            self.user_id = self.user.get("id", "")
            self.devices = await self.api_client.list_devices()
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            raise UpdateFailed(f"Error fetching initial data: {err}") from err

    async def _async_update_data(self) -> dict:
        """Poll mutable state."""
        try:
            await self.api_client.ensure_valid_token()
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            raise UpdateFailed(f"Error refreshing token: {err}") from err

        data: dict = {
            "schedules": {},
            "insights": {},
        }

        # Re-fetch devices each poll so zone/user changes surface.
        try:
            self.devices = await self.api_client.list_devices()
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            _LOGGER.warning("Failed to refresh device list: %s", err)

        # Rebuild the serial -> UUID map and sync the WS connections to
        # the current device list. Starting the WS manager here (rather
        # than in _async_setup) means it survives account topology
        # changes (devices added/removed) without a full reload.
        self._serial_to_id = {
            d["serial_number"]: d["id"]
            for d in self.devices
            if d.get("serial_number") and d.get("id")
        }
        self._ws_manager.sync_to_serials(list(self._serial_to_id.keys()))

        # Fetch the live snapshot for each device (zone on/temp + status).
        # GET /v1/devices does NOT include the `on` field; GET /v1/devices/
        # {serial}/live does. The /live path uses serial_number, not UUID.
        #
        # We still poll /live even with the WS in place — the WS is best-
        # effort and the periodic REST fetch guarantees the entities have
        # fresh state if the socket ever drops between polls. When the WS
        # is healthy the coordinator state is kept up to date by
        # async_set_updated_data from _handle_ws_message, so users don't
        # wait for the next poll to see their toggles reflected.
        new_live: dict[str, dict] = {}
        for device in self.devices:
            dev_id = device.get("id")
            serial = device.get("serial_number")
            if not dev_id or not serial:
                continue
            # Keep any WS-provided state until the REST fetch replaces it
            # — this avoids a flash of stale data between polls.
            if dev_id in self.live_devices and self._ws_manager.is_fresh(serial):
                new_live[dev_id] = self.live_devices[dev_id]
                continue
            try:
                new_live[dev_id] = await self.api_client.get_live_device(serial)
            except OrionAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (OrionApiError, OrionConnectionError) as err:
                _LOGGER.warning("Failed to fetch live state for %s: %s", serial, err)
                # Preserve whatever we already had rather than blanking it.
                if dev_id in self.live_devices:
                    new_live[dev_id] = self.live_devices[dev_id]
        self.live_devices = new_live

        try:
            data["schedules"] = await self.api_client.get_sleep_schedules()
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            _LOGGER.warning("Failed to fetch sleep schedules: %s", err)

        try:
            insights_days = self.config_entry.options.get(
                CONF_INSIGHTS_DAYS, DEFAULT_INSIGHTS_DAYS
            )
            data["insights"] = await self.api_client.get_insights(days=insights_days)
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            _LOGGER.warning("Failed to fetch insights: %s", err)

        return data

    def get_latest_session(self) -> dict | None:
        """Get the most recent sleep session from insights data."""
        insights = (self.data or {}).get("insights", {})
        insights_data = insights.get("data", {})
        if not insights_data:
            return None

        # Iterate dates in reverse chronological order
        for date_key in sorted(insights_data.keys(), reverse=True):
            day_data = insights_data[date_key]
            sessions = day_data.get("sessions", [])
            if sessions:
                return sessions[-1]
        return None

    def get_today_schedule(self) -> dict | None:
        """Get today's sleep schedule for the current user."""
        schedules = (self.data or {}).get("schedules", {})
        today = schedules.get("today_sleep_schedule", {})
        return today.get(self.user_id)

    def get_all_schedules(self) -> list[dict]:
        """Get all schedule entries for the current user."""
        schedules = (self.data or {}).get("schedules", {})
        all_schedules = schedules.get("schedules", {})
        return all_schedules.get(self.user_id, [])

    def is_any_schedule_active(self) -> bool:
        """Check if any schedule day has bedtime_is_active set."""
        for sched in self.get_all_schedules():
            if sched.get("bedtime_is_active"):
                return True
        return False

    # ── WebSocket integration ─────────────────────────────────────────

    @callback
    def _handle_ws_message(
        self, serial: str, msg_type: str, payload: dict[str, Any]
    ) -> None:
        """Merge a ``live_device.{snapshot,update}`` frame into state.

        Called from the WS receive loop. Both event types carry the same
        payload shape, so we treat them identically: the payload IS the
        new live state for the device. We also extract the today's
        schedule timeline when present, since it arrives only via WS.
        """
        if msg_type not in ("live_device.snapshot", "live_device.update"):
            # Any new event type we haven't accounted for — log once so
            # we know to update openapi.yaml / AGENTS.md.
            _LOGGER.debug(
                "Orion WS unexpected event type=%s serial=%s keys=%s",
                msg_type,
                serial,
                list(payload.keys()),
            )
            return

        dev_id = self._serial_to_id.get(serial)
        if not dev_id:
            _LOGGER.debug("Orion WS message for unknown serial %s; ignoring", serial)
            return

        # Merge in place so any fields present in the prior snapshot that
        # aren't repeated in this frame are preserved. In practice the
        # server includes the full payload every time, so this is mostly
        # a belt-and-suspenders guard.
        previous = self.live_devices.get(dev_id, {})
        merged = {**previous, **payload}
        self.live_devices[dev_id] = merged

        # Stash the timeline (today's scheduled actions) on the coordinator
        # data so sensors can read it without polling /v1/sleep-schedules
        # more aggressively. Only live_device.update carries this field.
        if msg_type == "live_device.update" and "timeline" in payload:
            data = dict(self.data or {})
            timelines = dict(data.get("ws_timelines", {}))
            timelines[dev_id] = payload.get("timeline") or []
            data["ws_timelines"] = timelines
            self.async_set_updated_data(data)
        else:
            # Snapshot — no timeline, still push so entities re-render.
            # async_set_updated_data is a no-op if called with the same
            # dict reference, so build a shallow copy.
            data = dict(self.data or {})
            self.async_set_updated_data(data)

    @callback
    def _handle_ws_state(self, serial: str, state: str) -> None:
        """Log WS connection-state transitions for diagnostics."""
        _LOGGER.debug("Orion WS %s -> %s", serial, state)

    def ws_state(self, serial: str) -> str:
        """Return the current WS state for a device (for diagnostics)."""
        return self._ws_manager.state(serial)

    def ws_last_message_at(self, serial: str) -> float:
        """Monotonic timestamp of the most recent WS frame, or 0."""
        return self._ws_manager.last_message_at(serial)

    async def async_shutdown(self) -> None:
        """Stop the WS manager before the coordinator is disposed."""
        await self._ws_manager.async_stop()
        await super().async_shutdown()

    # ── Live per-sensor helpers (fed by the WebSocket stream) ─────────
    #
    # ``live_device.{snapshot,update}`` payloads expose two in-topper
    # sensors at ``status.sensors.sensor1`` and ``status.sensors.sensor2``.
    # The zone->sensor mapping (sensor1 ~ zone_a vs. zone_b) has not yet
    # been verified on the wire, so we key on the raw sensor name and let
    # the user map them to sides in their automations.
    #
    # Observed payload shape (see openapi.yaml WsSensor):
    #   status, status_text, heart_rate, breath_rate, sign_of_asleep,
    #   sign_of_wake_up, timestamp, uptime, is_working, firmware_version,
    #   hardware_version
    #
    # Observed status_text values: "left_bed" (nobody on the topper) and
    # "normal" (someone on it, readings tracking). heart_rate/breath_rate
    # use 255 as a "no reading yet" sentinel and 0 when the bed is empty.

    # Sentinel value the topper reports for HR/BR when it has no reading
    # yet (e.g. the first second or two after someone sits down).
    _SENSOR_SENTINEL = 255

    def _sensor_block(self, device_id: str, sensor_name: str) -> dict[str, Any] | None:
        """Return the raw sensor payload or None if not yet seen."""
        live = self.live_devices.get(device_id)
        if not live:
            return None
        sensors = (live.get("status") or {}).get("sensors") or {}
        block = sensors.get(sensor_name)
        if not isinstance(block, dict):
            return None
        return block

    def sensor_status_text(self, device_id: str, sensor_name: str) -> str | None:
        block = self._sensor_block(device_id, sensor_name)
        if not block:
            return None
        text = block.get("status_text")
        return text if isinstance(text, str) else None

    def sensor_is_on_bed(self, device_id: str, sensor_name: str) -> bool | None:
        """Return occupancy for one topper sensor.

        ``status_text == "left_bed"`` -> empty; any other value means a
        person is on the bed. If we've never seen a frame yet, return
        None so HA shows the sensor as unknown rather than guessing.
        """
        text = self.sensor_status_text(device_id, sensor_name)
        if text is None:
            return None
        return text != "left_bed"

    def sensor_heart_rate(self, device_id: str, sensor_name: str) -> int | None:
        """Return the live HR for one sensor, mapping sentinels to None.

        * ``0`` when the bed is empty -> None (the value would mislead
          automations looking at raw BPM).
        * ``255`` is the topper's "no reading yet" sentinel -> None.
        * Any other value is returned as-is.
        """
        block = self._sensor_block(device_id, sensor_name)
        if not block:
            return None
        hr = block.get("heart_rate")
        if not isinstance(hr, (int, float)):
            return None
        hr = int(hr)
        if hr == 0 or hr == self._SENSOR_SENTINEL:
            return None
        return hr

    def sensor_breath_rate(self, device_id: str, sensor_name: str) -> int | None:
        """Return the live breath rate for one sensor, with sentinel handling."""
        block = self._sensor_block(device_id, sensor_name)
        if not block:
            return None
        br = block.get("breath_rate")
        if not isinstance(br, (int, float)):
            return None
        br = int(br)
        if br == 0 or br == self._SENSOR_SENTINEL:
            return None
        return br

    def sensor_is_working(self, device_id: str, sensor_name: str) -> bool | None:
        block = self._sensor_block(device_id, sensor_name)
        if not block:
            return None
        val = block.get("is_working")
        return bool(val) if val is not None else None

    def is_user_away(self, device_id: str) -> bool | None:
        """Check whether the user is currently marked away on the device.

        The server signals away-mode by nulling out ``zones[*].user`` on
        the device returned from ``GET /v1/devices``; when the user is
        present each zone carries a populated ``user`` object. Verified
        by toggling ``POST /v1/sleep-configurations/user-away`` and
        re-fetching the device list.

        This is **distinct from device power state** (``is_device_on``).
        The mattress can be powered off while the user is still present
        (e.g. outside the schedule window), so deriving away-mode from
        the power state produces a desynced switch and makes
        ``set_user_away(is_away=False)`` fail with
        ``400 "User has no previous device to return to"`` when the user
        was already present.
        """
        for device in self.devices:
            if device.get("id") != device_id:
                continue
            zones = device.get("zones") or []
            if not zones:
                return None
            # User is present if any zone has a user object attached; away
            # only if every zone's user is null. The app treats a partial
            # state as "present" (safer default — avoids a 400 from the
            # user-away endpoint).
            for zone in zones:
                if zone.get("user"):
                    return False
            return True
        return None

    def is_device_on(self, device_id: str) -> bool | None:
        """Check if the device is on.

        Reads the per-zone `on` field from the live snapshot
        (`GET /v1/devices/{serial}/live`). Returns True if any zone is
        on, False if all zones report off, and None if no live snapshot
        is available yet.
        """
        live = self.live_devices.get(device_id)
        if not live:
            return None
        zones = live.get("zones", [])
        if not zones:
            return None
        saw_any = False
        any_on = False
        for zone in zones:
            if "on" in zone:
                saw_any = True
                if zone.get("on"):
                    any_on = True
        return any_on if saw_any else None
