"""Base entity for Orion Sleep."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OrionDataUpdateCoordinator


class OrionBaseEntity(CoordinatorEntity[OrionDataUpdateCoordinator]):
    """Base entity for Orion Sleep."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OrionDataUpdateCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        device = self._get_device()
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("name", "Orion Sleep"),
            manufacturer="Orion Longevity",
            model=device.get("model", "Orion Sleep"),
            serial_number=device.get("serial_number"),
        )

    def _get_device(self) -> dict:
        """Find the device dict from the coordinator's device list."""
        for d in self.coordinator.devices:
            if d.get("id") == self._device_id:
                return d
        return {}
