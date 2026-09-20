"""Services for the Orion Sleep integration.

`orion_sleep.set_schedule` sets the recurring bedtime / wake-up *times*; the
Number entities already cover the schedule temperatures.

Sends PUT /v1/sleep-schedules with {"schedules": [{"day": N, ...}]}, the same
partial-update shape used for temperatures, so unspecified fields are kept.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_SCHEDULE = "set_schedule"
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("days", default=ALL_DAYS): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))],
            vol.Length(min=1),
        ),
        vol.Optional("bedtime"): cv.matches_regex(TIME_PATTERN),
        vol.Optional("wakeup"): cv.matches_regex(TIME_PATTERN),
        vol.Optional("bedtime_is_active"): cv.boolean,
        vol.Optional("wakeup_is_active"): cv.boolean,
        vol.Optional("auto_turn_off"): cv.boolean,
    }
)

_FIELDS = ("bedtime", "wakeup", "bedtime_is_active", "wakeup_is_active", "auto_turn_off")


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register Orion Sleep services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        return

    async def _handle_set_schedule(call: ServiceCall) -> None:
        fields = {k: call.data[k] for k in _FIELDS if k in call.data}
        if not fields:
            raise HomeAssistantError(
                "orion_sleep.set_schedule needs at least one of: " + ", ".join(_FIELDS)
            )

        entries = hass.config_entries.async_entries(DOMAIN)
        coordinators = [
            c for c in (getattr(e, "runtime_data", None) for e in entries) if c
        ]
        if not coordinators:
            raise HomeAssistantError("No loaded Orion Sleep config entry found")

        payload = {"schedules": [{"day": day, **fields} for day in call.data["days"]]}
        _LOGGER.debug("orion_sleep.set_schedule payload: %s", payload)

        for coordinator in coordinators:
            try:
                await coordinator.api_client.update_sleep_schedule(payload)
            except Exception as err:  # noqa: BLE001 - surface as a HA error
                raise HomeAssistantError(f"Orion schedule update failed: {err}") from err
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, _handle_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
