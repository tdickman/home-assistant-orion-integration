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
            model="Orion Sleep",
            sw_version=device.get("firmwareVersion"),
        )

    def _get_device(self) -> dict:
        """Find the device dict from the coordinator's device list."""
        for d in self.coordinator.devices:
            if d.get("deviceId") == self._device_id or d.get("id") == self._device_id:
                return d
        return {}

    def _get_sleep_config(self) -> dict | None:
        """Find the sleep config for this device from coordinator data."""
        for cfg in (self.coordinator.data or {}).get("sleep_configs", []):
            if cfg.get("deviceId") == self._device_id:
                return cfg
        return None
