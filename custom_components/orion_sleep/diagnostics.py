"""Diagnostics support for Orion Sleep."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import OrionDataUpdateCoordinator

TO_REDACT = {
    "access_token",
    "refresh_token",
    "email",
    "phone",
    "auth_value",
    "userId",
    "sessionId",
    "name",
    "firstName",
    "lastName",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data

    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "config_entry_options": dict(entry.options),
        "coordinator_data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "devices": async_redact_data(coordinator.devices, TO_REDACT),
        "user": async_redact_data(coordinator.user, TO_REDACT),
    }
