"""Base entity for the Ubee Cable Modem integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import UbeeCoordinator


class UbeeEntity(CoordinatorEntity[UbeeCoordinator]):
    """Common device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UbeeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Ubee Cable Modem",
            manufacturer=MANUFACTURER,
            model=DEFAULT_MODEL,
            configuration_url=coordinator.client.status_url,
        )
