"""Binary sensors for the Ubee Cable Modem integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UbeeConfigEntry
from .const import CONF_UNCORRECTABLE_THRESHOLD, DEFAULT_UNCORRECTABLE_THRESHOLD
from .coordinator import UbeeCoordinator
from .entity import UbeeEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: UbeeConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            ModemReachableSensor(c),
            ModemOperationalSensor(c),
            ChannelsUnlockedSensor(c),
            UncorrectableErrorsSensor(c),
        ]
    )


class ModemReachableSensor(UbeeEntity, BinarySensorEntity):
    """On when the status page could be fetched on the last poll.

    Stays available when polling fails so dropouts show up in history.
    """

    _attr_name = "Reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: UbeeCoordinator) -> None:
        super().__init__(coordinator, "reachable")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "unreachable_count": c.unreachable_count,
            "last_unreachable": c.last_unreachable.isoformat() if c.last_unreachable else None,
            "auth_method": c.client.auth_method,
        }


class ModemOperationalSensor(UbeeEntity, BinarySensorEntity):
    """On when the startup procedure reports Connectivity State OK / Operational."""

    _attr_name = "Operational"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: UbeeCoordinator) -> None:
        super().__init__(coordinator, "operational")

    @property
    def is_on(self) -> bool | None:
        startup = self.coordinator.data.status.startup
        conn = startup.get("Connectivity State")
        boot = startup.get("Boot State")
        if conn is None:
            return None
        ok = conn.status.upper() == "OK" and "operational" in conn.comment.lower()
        if boot is not None:
            ok = ok and boot.status.upper() == "OK"
        return ok


class ChannelsUnlockedSensor(UbeeEntity, BinarySensorEntity):
    """Problem when any downstream or upstream channel is not locked."""

    _attr_name = "Channel lock problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: UbeeCoordinator) -> None:
        super().__init__(coordinator, "channel_lock_problem")

    @property
    def is_on(self) -> bool:
        d = self.coordinator.data
        return any(not c.locked for c in d.downstream.values()) or any(
            not c.locked for c in d.upstream.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        return {
            "downstream_unlocked": [cid for cid, c in d.downstream.items() if not c.locked],
            "upstream_unlocked": [cid for cid, c in d.upstream.items() if not c.locked],
        }


class UncorrectableErrorsSensor(UbeeEntity, BinarySensorEntity):
    """Problem when uncorrectable codewords per minute exceed the configured threshold."""

    _attr_name = "Uncorrectable errors"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: UbeeCoordinator) -> None:
        super().__init__(coordinator, "uncorrectable_problem")

    @property
    def _threshold(self) -> float:
        return float(
            self.coordinator.config_entry.options.get(
                CONF_UNCORRECTABLE_THRESHOLD, DEFAULT_UNCORRECTABLE_THRESHOLD
            )
        )

    @property
    def is_on(self) -> bool | None:
        rate = self.coordinator.data.uncorrectable_per_min
        if rate is None:
            return None
        return rate > self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "threshold_per_min": self._threshold,
            "rate_per_min": self.coordinator.data.uncorrectable_per_min,
        }
