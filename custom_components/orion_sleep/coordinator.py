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
        self.user: dict = {}

    async def _async_setup(self) -> None:
        """Load one-time data: user profile, device list."""
        try:
            self.user = await self.api_client.get_current_user()
            self.devices = await self.api_client.list_devices()
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            raise UpdateFailed(f"Error fetching initial data: {err}") from err

    async def _async_update_data(self) -> dict:
        """Poll mutable state."""
        try:
            await self.api_client.ensure_valid_token()

            sleep_configs = await self.api_client.get_sleep_config_devices()
            session_state = await self.api_client.get_session_state()
            schedules = await self.api_client.get_sleep_schedules()

            insights_days = self.config_entry.options.get(
                CONF_INSIGHTS_DAYS, DEFAULT_INSIGHTS_DAYS
            )
            insights = await self.api_client.get_insights(days=insights_days)

            return {
                "sleep_configs": sleep_configs,
                "session_state": session_state,
                "schedules": schedules,
                "insights": insights,
            }
        except OrionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OrionApiError, OrionConnectionError) as err:
            raise UpdateFailed(f"Error fetching Orion data: {err}") from err
