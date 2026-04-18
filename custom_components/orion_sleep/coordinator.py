"""DataUpdateCoordinator for Orion Sleep."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OrionApiClient, OrionApiError, OrionAuthError, OrionConnectionError
from .const import (
    CONF_INSIGHTS_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_INSIGHTS_DAYS,
    DEFAULT_SCAN_INTERVAL,
)

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
        # Live snapshots keyed by device id (UUID). Populated each poll
        # from GET /v1/devices/{serial}/live — this is where zone on/temp
        # actually live, since GET /v1/devices doesn't expose them.
        self.live_devices: dict[str, dict] = {}
        self.user: dict = {}
        self.user_id: str = ""

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

        # Fetch the live snapshot for each device (zone on/temp + status).
        # GET /v1/devices does NOT include the `on` field; GET /v1/devices/
        # {serial}/live does. The /live path uses serial_number, not UUID.
        new_live: dict[str, dict] = {}
        for device in self.devices:
            dev_id = device.get("id")
            serial = device.get("serial_number")
            if not dev_id or not serial:
                continue
            try:
                new_live[dev_id] = await self.api_client.get_live_device(serial)
            except OrionAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (OrionApiError, OrionConnectionError) as err:
                _LOGGER.warning("Failed to fetch live state for %s: %s", serial, err)
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
