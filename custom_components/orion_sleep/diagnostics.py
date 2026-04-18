"""Diagnostics support for Orion Sleep."""

from __future__ import annotations

import time
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
    "user_id",
    "session_id",
    "id",
    "name",
    "first_name",
    "last_name",
    "serial_number",
    "intercom_jwt",
    "dob",
    "profile_image_url",
    # Network PII from the live-device WS payload.
    "ip",
    "mac",
    # SSID (appears as `name` inside status.network but redacted above too).
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data

    now_monotonic = time.monotonic()
    # Use a list of objects rather than a dict keyed by serial, so the
    # async_redact_data call below scrubs the serial_number field.
    websocket_summary: list[dict[str, Any]] = []
    for device in coordinator.devices:
        serial = device.get("serial_number")
        if not serial:
            continue
        last_at = coordinator.ws_last_message_at(serial)
        age = (now_monotonic - last_at) if last_at else None
        websocket_summary.append(
            {
                "serial_number": serial,
                "state": coordinator.ws_state(serial),
                "seconds_since_last_message": age,
            }
        )

    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "config_entry_options": dict(entry.options),
        "coordinator_data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "devices": async_redact_data(coordinator.devices, TO_REDACT),
        "live_devices": async_redact_data(dict(coordinator.live_devices), TO_REDACT),
        "user": async_redact_data(coordinator.user, TO_REDACT),
        "websocket": async_redact_data(websocket_summary, TO_REDACT),
    }
