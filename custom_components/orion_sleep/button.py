"""Button platform for Orion Sleep."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import OrionDataUpdateCoordinator
from .entity import OrionBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Orion Sleep button entities."""
    coordinator: OrionDataUpdateCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = []

    for device in coordinator.devices:
        device_id = device.get("id")
        if not device_id:
            continue
        entities.append(OrionRebootButton(coordinator, device_id))

    async_add_entities(entities)


class OrionRebootButton(OrionBaseEntity, ButtonEntity):
    """Button to reboot the Orion device."""

    _attr_translation_key = "reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_reboot"

    async def async_press(self) -> None:
        """Trigger a device reboot."""
        await self.coordinator.api_client.device_action(
            self._device_id, "device_reboot"
        )
