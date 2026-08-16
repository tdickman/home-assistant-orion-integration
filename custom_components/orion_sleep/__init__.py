"""The Orion Sleep integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OrionApiClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_PARTNER_ACCESS_TOKEN,
    CONF_PARTNER_EXPIRES_AT,
    CONF_PARTNER_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN,
)
from .coordinator import OrionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Orion Sleep from a config entry."""
    session = async_get_clientsession(hass)
    api_client = OrionApiClient(
        session=session,
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        expires_at=entry.data[CONF_EXPIRES_AT],
    )

    # Register token refresh callback to persist new tokens
    def on_token_refresh(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: access_token,
                CONF_REFRESH_TOKEN: refresh_token,
                CONF_EXPIRES_AT: expires_at,
            },
        )

    api_client.set_token_refresh_callback(on_token_refresh)

    partner_client: OrionApiClient | None = None
    if CONF_PARTNER_ACCESS_TOKEN in entry.data:
        partner_client = OrionApiClient(
            session=session,
            access_token=entry.data[CONF_PARTNER_ACCESS_TOKEN],
            refresh_token=entry.data.get(CONF_PARTNER_REFRESH_TOKEN, ""),
            expires_at=entry.data.get(CONF_PARTNER_EXPIRES_AT, 0),
        )

        def on_partner_token_refresh(
            access_token: str, refresh_token: str, expires_at: float
        ) -> None:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_PARTNER_ACCESS_TOKEN: access_token,
                    CONF_PARTNER_REFRESH_TOKEN: refresh_token,
                    CONF_PARTNER_EXPIRES_AT: expires_at,
                },
            )

        partner_client.set_token_refresh_callback(on_partner_token_refresh)

    coordinator = OrionDataUpdateCoordinator(hass, entry, api_client, partner_client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload on options change
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when options actually changed.

    ``add_update_listener`` fires on any entry update, including token-refresh
    callbacks that write new tokens to ``entry.data``. Reloading on those
    data-only updates tears the integration down mid-operation, so we guard
    against it by comparing ``entry.options`` to the snapshot taken at setup.
    """
    coordinator: OrionDataUpdateCoordinator | None = getattr(
        entry, "runtime_data", None
    )
    if coordinator is not None and entry.options == coordinator.options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: OrionDataUpdateCoordinator | None = getattr(
            entry, "runtime_data", None
        )
        if coordinator is not None:
            # Close the live-device WebSockets cleanly (code 1001), matching
            # the Android app's background-shutdown behavior.
            await coordinator.async_shutdown()
    return unloaded
