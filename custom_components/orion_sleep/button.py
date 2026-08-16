"""Button platform for Orion Sleep — one-shot device actions.

Each button is *gated* on the device's `permissions.allowed_actions` but
*dispatched* to its own endpoint. Those are two different things, and
conflating them is what broke the first cut of this platform:

  `allowed_actions` is a **UI capability list** — the right question for
  "should this control exist?", the wrong answer for "what do I call?".
  `POST /v1/devices/{id}/action` accepts only `reboot` / `forget_wifi`
  (measured 2026-07-26); everything else has a dedicated endpoint.

⚠️ `split` and `swap` are NOT exposed despite appearing in
`allowed_actions`. `openapi.yaml` documents them as
`POST /v1/sleep-configurations/user-{split-user-zones,swap-user-sides}`,
but both return a **bare `404 Not Found`** with no JSON body — the route
does not exist on the server, exactly like `/v1/sleep-configurations/
devices` already does. Measured 2026-07-26. Note the distinction: an
app-level miss returns `404 {"success":false,"error":"Device not found"}`,
a missing *route* returns bare text. Where the app really performs these
is unknown; `PUT /v1/sleep-configurations/user-update`
({deviceId, userId, side}) is the plausible candidate for swap, but it is
untested and sits in the same unverified block of the spec.

⚠️ `device_forget_wifi` and `device_deactivate` are permitted by the
account and deliberately NOT exposed. Forgetting WiFi strands the bed —
the network is the only path to it, there is no BLE surface and every TCP
port is closed — and deactivate unpairs it. Neither is recoverable from
Home Assistant. `device_reset` the server does not grant.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import OrionApiClient
from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class OrionButtonDef:
    """A button, its display gate, and how it is actually invoked."""

    key: str
    name: str
    icon: str
    # Capability name in permissions.allowed_actions — the DISPLAY gate.
    gate: str
    # How to perform it — the DISPATCH. Takes (client, device_id, serial).
    # Both identifiers are passed because the endpoints disagree about
    # which one they want: /action needs the SERIAL, while the
    # sleep-configuration endpoints take deviceId in the body.
    call: Callable[[OrionApiClient, str, str], Awaitable[dict]]


BUTTONS: tuple[OrionButtonDef, ...] = (
    OrionButtonDef(
        key="reboot",
        name="Reboot Control Tower",
        icon="mdi:restart",
        gate="device_reboot",
        # Bare "reboot" (not "device_reboot"), keyed as action_type,
        # addressed by SERIAL. All three were wrong in the first cut.
        call=lambda client, device_id, serial: client.device_action(
            device_serial=serial, action="reboot"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a button per permitted device action."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[OrionActionButton] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        allowed = coordinator.device_allowed_actions(device_id)
        for definition in BUTTONS:
            if definition.gate not in allowed:
                _LOGGER.debug(
                    "Orion device %s does not permit '%s'; button not created",
                    device_id, definition.gate,
                )
                continue
            entities.append(OrionActionButton(coordinator, device_id, definition))

    async_add_entities(entities)


class OrionActionButton(OrionBaseEntity, ButtonEntity):
    """Fires one device action. No state — the API exposes none for these."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
        definition: OrionButtonDef,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._def = definition
        self._attr_name = definition.name
        self._attr_icon = definition.icon
        self._attr_unique_id = f"{device_id}_action_{definition.key}"

    async def async_press(self) -> None:
        """Invoke this button's own endpoint."""
        _LOGGER.info(
            "Orion button '%s' pressed on device %s", self._def.key, self._device_id
        )
        serial = self._get_device().get("serial_number")
        if not serial:
            raise HomeAssistantError(
                f"No serial_number for Orion device {self._device_id}"
            )
        await self._def.call(self.coordinator.api_client, self._device_id, str(serial))
        await self.coordinator.async_request_refresh()
