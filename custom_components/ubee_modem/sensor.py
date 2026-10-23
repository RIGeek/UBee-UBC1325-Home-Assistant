"""Sensors for the Ubee Cable Modem integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from statistics import mean
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS, EntityCategory, UnitOfFrequency
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UbeeConfigEntry
from .coordinator import ModemData, UbeeCoordinator
from .entity import UbeeEntity

DBMV = "dBmV"
CODEWORDS = "codewords"
CODEWORDS_PER_MIN = "codewords/min"


@dataclass(frozen=True, kw_only=True)
class UbeeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ModemData], Any]
    attrs_fn: Callable[[ModemData], dict[str, Any]] | None = None


def _step(name: str, part: str) -> Callable[[ModemData], Any]:
    def fn(d: ModemData) -> Any:
        step = d.status.startup.get(name)
        return getattr(step, part) if step else None

    return fn


def _extreme(values: list[tuple[int, float | None]], pick) -> tuple[int | None, float | None]:
    vals = [(cid, v) for cid, v in values if v is not None]
    if not vals:
        return None, None
    return pick(vals, key=lambda x: x[1])


def _ds(d: ModemData, attr: str) -> list[tuple[int, float | None]]:
    return [(cid, getattr(ch, attr)) for cid, ch in d.downstream.items()]


def _us(d: ModemData) -> list[tuple[int, float | None]]:
    return [(cid, ch.power_dbmv) for cid, ch in d.upstream.items()]


def _avg(values: list[tuple[int, float | None]]) -> float | None:
    v = [x for _, x in values if x is not None]
    return round(mean(v), 2) if v else None


def _worst_channels(d: ModemData) -> dict[str, Any]:
    deltas = d.channel_uncorrectable_delta
    if not deltas:
        return {}
    top = sorted(deltas.items(), key=lambda x: x[1], reverse=True)[:5]
    return {
        "worst_channels": {f"ch_{cid}": n for cid, n in top if n > 0},
        "channels_with_errors": sum(1 for n in deltas.values() if n > 0),
        "channels_reporting": len(deltas),
    }


def _locked_attrs(d: ModemData, chans: dict) -> dict[str, Any]:
    return {
        "total": len(chans),
        "ofdm": sum(1 for c in chans.values() if c.is_ofdm),
        "sc_qam": sum(1 for c in chans.values() if not c.is_ofdm),
        "not_locked": [cid for cid, c in chans.items() if not c.locked],
    }


MODEM_SENSORS: tuple[UbeeSensorDescription, ...] = (
    UbeeSensorDescription(
        key="connectivity_state",
        name="Connectivity state",
        icon="mdi:lan-connect",
        value_fn=_step("Connectivity State", "comment"),
        attrs_fn=lambda d: {"status": _step("Connectivity State", "status")(d)},
    ),
    UbeeSensorDescription(
        key="boot_state",
        name="Boot state",
        icon="mdi:restart",
        value_fn=_step("Boot State", "comment"),
        attrs_fn=lambda d: {"status": _step("Boot State", "status")(d)},
    ),
    UbeeSensorDescription(
        key="ds_channels_locked",
        name="Downstream channels locked",
        icon="mdi:download-network",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for c in d.downstream.values() if c.locked),
        attrs_fn=lambda d: _locked_attrs(d, d.downstream),
    ),
    UbeeSensorDescription(
        key="us_channels_locked",
        name="Upstream channels locked",
        icon="mdi:upload-network",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for c in d.upstream.values() if c.locked),
        attrs_fn=lambda d: _locked_attrs(d, d.upstream),
    ),
    UbeeSensorDescription(
        key="ds_snr_min",
        name="Downstream SNR minimum",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _extreme(_ds(d, "snr_db"), min)[1],
        attrs_fn=lambda d: {"channel_id": _extreme(_ds(d, "snr_db"), min)[0]},
    ),
    UbeeSensorDescription(
        key="ds_snr_avg",
        name="Downstream SNR average",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _avg(_ds(d, "snr_db")),
    ),
    UbeeSensorDescription(
        key="ds_power_min",
        name="Downstream power minimum",
        icon="mdi:signal",
        native_unit_of_measurement=DBMV,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _extreme(_ds(d, "power_dbmv"), min)[1],
        attrs_fn=lambda d: {"channel_id": _extreme(_ds(d, "power_dbmv"), min)[0]},
    ),
    UbeeSensorDescription(
        key="ds_power_max",
        name="Downstream power maximum",
        icon="mdi:signal",
        native_unit_of_measurement=DBMV,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _extreme(_ds(d, "power_dbmv"), max)[1],
        attrs_fn=lambda d: {"channel_id": _extreme(_ds(d, "power_dbmv"), max)[0]},
    ),
    UbeeSensorDescription(
        key="us_power_min",
        name="Upstream transmit power minimum",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=DBMV,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _extreme(_us(d), min)[1],
        attrs_fn=lambda d: {"channel_id": _extreme(_us(d), min)[0]},
    ),
    UbeeSensorDescription(
        key="us_power_max",
        name="Upstream transmit power maximum",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=DBMV,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _extreme(_us(d), max)[1],
        attrs_fn=lambda d: {"channel_id": _extreme(_us(d), max)[0]},
    ),
    UbeeSensorDescription(
        key="total_corrected",
        name="Corrected codewords",
        icon="mdi:check-decagram-outline",
        native_unit_of_measurement=CODEWORDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.total_corrected,
    ),
    UbeeSensorDescription(
        key="total_uncorrectable",
        name="Uncorrectable codewords",
        icon="mdi:alert-decagram-outline",
        native_unit_of_measurement=CODEWORDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.total_uncorrectable,
    ),
    UbeeSensorDescription(
        key="corrected_rate",
        name="Corrected codeword rate",
        icon="mdi:chart-line",
        native_unit_of_measurement=CODEWORDS_PER_MIN,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.corrected_per_min,
    ),
    UbeeSensorDescription(
        key="uncorrectable_rate",
        name="Uncorrectable codeword rate",
        icon="mdi:chart-line-variant",
        native_unit_of_measurement=CODEWORDS_PER_MIN,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.uncorrectable_per_min,
        attrs_fn=_worst_channels,
    ),
    UbeeSensorDescription(
        key="ds_acquire_frequency",
        name="Primary downstream frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.MEGAHERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _mhz(_step("Acquire Downstream Channel", "status")(d)),
        attrs_fn=lambda d: {"lock": _step("Acquire Downstream Channel", "comment")(d)},
    ),
    UbeeSensorDescription(
        key="config_file",
        name="Configuration file",
        icon="mdi:file-cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_step("Configuration File", "comment"),
        attrs_fn=lambda d: {"status": _step("Configuration File", "status")(d)},
    ),
    UbeeSensorDescription(
        key="security",
        name="Security",
        icon="mdi:shield-lock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_step("Security", "status"),
    ),
    UbeeSensorDescription(
        key="prov_mode",
        name="IP provisioning mode",
        icon="mdi:ip-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.prov_mode,
    ),
    UbeeSensorDescription(
        key="cm_ipv4",
        name="Modem IPv4 address",
        icon="mdi:ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.status.ipv4,
    ),
    UbeeSensorDescription(
        key="lease_obtained",
        name="DHCP lease obtained",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.lease_obtained,
    ),
    UbeeSensorDescription(
        key="lease_expires",
        name="DHCP lease expires",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.lease_expires,
    ),
    UbeeSensorDescription(
        key="modem_time",
        name="Modem system time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.status.system_time,
    ),
)


def _mhz(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return round(float(text.split()[0]) / 1_000_000, 3)
    except (ValueError, IndexError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: UbeeConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UbeeModemSensor(coordinator, desc) for desc in MODEM_SENSORS)

    known_ds: set[int] = set()
    known_us: set[int] = set()

    @callback
    def _add_channels() -> None:
        data = coordinator.data
        if data is None:
            return
        new: list[SensorEntity] = []
        for cid, ch in data.downstream.items():
            if cid in known_ds:
                continue
            known_ds.add(cid)
            new.extend(
                DownstreamChannelSensor(coordinator, cid, ch.is_ofdm, kind)
                for kind in DS_KINDS
            )
        for cid, ch in data.upstream.items():
            if cid in known_us:
                continue
            known_us.add(cid)
            new.append(UpstreamChannelSensor(coordinator, cid, ch.is_ofdm))
        if new:
            async_add_entities(new)

    _add_channels()
    entry.async_on_unload(coordinator.async_add_listener(_add_channels))


class UbeeModemSensor(UbeeEntity, SensorEntity):
    """Modem-wide sensor."""

    entity_description: UbeeSensorDescription

    def __init__(self, coordinator: UbeeCoordinator, desc: UbeeSensorDescription) -> None:
        super().__init__(coordinator, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


# kind -> (name, unit, device_class, state_class, enabled_by_default, icon)
DS_KINDS: dict[str, tuple] = {
    "snr": ("SNR", SIGNAL_STRENGTH_DECIBELS, SensorDeviceClass.SIGNAL_STRENGTH,
            SensorStateClass.MEASUREMENT, True, None),
    "power": ("power", DBMV, None, SensorStateClass.MEASUREMENT, True, "mdi:signal"),
    "corrected": ("corrected", CODEWORDS, None, SensorStateClass.TOTAL_INCREASING, False,
                  "mdi:check-decagram-outline"),
    "uncorrectable": ("uncorrectable", CODEWORDS, None, SensorStateClass.TOTAL_INCREASING,
                      False, "mdi:alert-decagram-outline"),
}


class DownstreamChannelSensor(UbeeEntity, SensorEntity):
    """Per-downstream-channel sensor, keyed by channel ID."""

    def __init__(self, coordinator: UbeeCoordinator, cid: int, ofdm: bool, kind: str) -> None:
        super().__init__(coordinator, f"ds_{cid}_{kind}")
        self._cid = cid
        self._kind = kind
        label, unit, dclass, sclass, enabled, icon = DS_KINDS[kind]
        prefix = f"DS {'OFDM ' if ofdm else ''}ch {cid}"
        self._attr_name = f"{prefix} {label}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = dclass
        self._attr_state_class = sclass
        self._attr_entity_registry_enabled_default = enabled
        self._attr_icon = icon
        if kind in ("snr", "power"):
            self._attr_suggested_display_precision = 1

    @property
    def _ch(self):
        data = self.coordinator.data
        return data.downstream.get(self._cid) if data else None

    @property
    def available(self) -> bool:
        return super().available and self._ch is not None

    @property
    def native_value(self) -> Any:
        ch = self._ch
        if ch is None:
            return None
        return {
            "snr": ch.snr_db,
            "power": ch.power_dbmv,
            "corrected": ch.corrected,
            "uncorrectable": ch.uncorrectable,
        }[self._kind]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        ch = self._ch
        if ch is None:
            return None
        attrs: dict[str, Any] = {
            "channel_index": ch.index,
            "channel_type": ch.channel_type,
            "frequency_mhz": ch.frequency_mhz,
            "width_mhz": ch.width_mhz,
            "modulation": ch.modulation,
            "lock_status": ch.lock_status,
            "bonding_status": ch.bonding_status,
        }
        data = self.coordinator.data
        if self._kind == "uncorrectable":
            attrs["since_last_poll"] = data.channel_uncorrectable_delta.get(self._cid)
            attrs["unerrored"] = ch.unerrored
        elif self._kind == "corrected":
            attrs["since_last_poll"] = data.channel_corrected_delta.get(self._cid)
        return attrs


class UpstreamChannelSensor(UbeeEntity, SensorEntity):
    """Per-upstream-channel transmit level."""

    _attr_native_unit_of_measurement = DBMV
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator: UbeeCoordinator, cid: int, ofdm: bool) -> None:
        super().__init__(coordinator, f"us_{cid}_power")
        self._cid = cid
        self._attr_name = f"US {'OFDMA ' if ofdm else ''}ch {cid} transmit power"

    @property
    def _ch(self):
        data = self.coordinator.data
        return data.upstream.get(self._cid) if data else None

    @property
    def available(self) -> bool:
        return super().available and self._ch is not None

    @property
    def native_value(self) -> float | None:
        ch = self._ch
        return ch.power_dbmv if ch else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        ch = self._ch
        if ch is None:
            return None
        return {
            "channel_index": ch.index,
            "channel_type": ch.channel_type,
            "frequency_mhz": ch.frequency_mhz,
            "width_mhz": ch.width_mhz,
            "modulation": ch.modulation,
            "lock_status": ch.lock_status,
            "bonding_status": ch.bonding_status,
        }
