"""Data update coordinator for the Ubee Cable Modem integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import UbeeAuthError, UbeeError, UbeeModemClient, UbeeOtherDeviceError
from .const import DOMAIN, EVENT_COUNTERS_RESET, EVENT_RECOVERED, EVENT_UNREACHABLE
from .parser import DownstreamChannel, ModemStatus, UpstreamChannel

_LOGGER = logging.getLogger(__name__)


@dataclass
class ModemData:
    """Parsed status plus values derived across polls."""

    status: ModemStatus
    downstream: dict[int, DownstreamChannel]
    upstream: dict[int, UpstreamChannel]
    total_corrected: int
    total_uncorrectable: int
    corrected_per_min: float | None = None
    uncorrectable_per_min: float | None = None
    channel_uncorrectable_delta: dict[int, int] = field(default_factory=dict)
    channel_corrected_delta: dict[int, int] = field(default_factory=dict)
    counters_reset: bool = False
    lease_obtained: datetime | None = None
    lease_expires: datetime | None = None
    fetched_at: datetime | None = None


class UbeeCoordinator(DataUpdateCoordinator[ModemData]):
    """Poll the modem status page."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UbeeModemClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            always_update=True,
        )
        self.client = client
        self._prev: ModemData | None = None
        self._prev_mono: float | None = None
        self._was_unreachable = False
        self.unreachable_since: datetime | None = None
        self.last_unreachable: datetime | None = None
        self.unreachable_count = 0
        self.other_device_answering = False
        self.last_error: str | None = None

    async def _async_update_data(self) -> ModemData:
        try:
            status = await self.client.async_get_status()
        except UbeeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UbeeError as err:
            self.other_device_answering = isinstance(err, UbeeOtherDeviceError)
            self.last_error = str(err)
            self._mark_unreachable(str(err))
            raise UpdateFailed(str(err)) from err

        self.other_device_answering = False
        self.last_error = None
        self._mark_reachable()
        now_mono = time.monotonic()
        now = dt_util.utcnow()

        ds = {c.channel_id: c for c in status.downstream}
        us = {c.channel_id: c for c in status.upstream}
        total_corr = sum(c.corrected or 0 for c in status.downstream)
        total_unc = sum(c.uncorrectable or 0 for c in status.downstream)

        data = ModemData(
            status=status,
            downstream=ds,
            upstream=us,
            total_corrected=total_corr,
            total_uncorrectable=total_unc,
            fetched_at=now,
        )

        # Lease timestamps, anchored to HA's clock (the modem clock may drift).
        if status.ipv4_lease_expires_in is not None:
            data.lease_expires = (now + status.ipv4_lease_expires_in).replace(second=0, microsecond=0)
            if status.ipv4_lease_duration is not None:
                obtained = now - (status.ipv4_lease_duration - status.ipv4_lease_expires_in)
                data.lease_obtained = obtained.replace(second=0, microsecond=0)

        prev = self._prev
        if prev is not None and self._prev_mono is not None:
            minutes = max((now_mono - self._prev_mono) / 60.0, 1e-6)
            if total_corr < prev.total_corrected or total_unc < prev.total_uncorrectable:
                # Counters went backwards: modem rebooted or re-registered.
                data.counters_reset = True
                _LOGGER.warning(
                    "Ubee modem codeword counters reset (modem reboot or re-registration)"
                )
                self.hass.bus.async_fire(
                    EVENT_COUNTERS_RESET,
                    {
                        "entry_id": self.config_entry.entry_id,
                        "previous_uncorrectable": prev.total_uncorrectable,
                        "current_uncorrectable": total_unc,
                    },
                )
            else:
                d_corr = total_corr - prev.total_corrected
                d_unc = total_unc - prev.total_uncorrectable
                data.corrected_per_min = round(d_corr / minutes, 2)
                data.uncorrectable_per_min = round(d_unc / minutes, 2)
                for cid, ch in ds.items():
                    old = prev.downstream.get(cid)
                    if old is None:
                        continue
                    if (ch.uncorrectable or 0) >= (old.uncorrectable or 0):
                        data.channel_uncorrectable_delta[cid] = (ch.uncorrectable or 0) - (
                            old.uncorrectable or 0
                        )
                    if (ch.corrected or 0) >= (old.corrected or 0):
                        data.channel_corrected_delta[cid] = (ch.corrected or 0) - (
                            old.corrected or 0
                        )

        self._prev = data
        self._prev_mono = now_mono
        return data

    def _mark_unreachable(self, reason: str) -> None:
        if not self._was_unreachable:
            self._was_unreachable = True
            self.unreachable_since = dt_util.utcnow()
            self.last_unreachable = self.unreachable_since
            self.unreachable_count += 1
            self.hass.bus.async_fire(
                EVENT_UNREACHABLE,
                {
                    "entry_id": self.config_entry.entry_id,
                    "reason": reason,
                    "other_device_answering": self.other_device_answering,
                },
            )

    def _mark_reachable(self) -> None:
        if self._was_unreachable:
            down_for = (
                (dt_util.utcnow() - self.unreachable_since).total_seconds()
                if self.unreachable_since
                else None
            )
            self.hass.bus.async_fire(
                EVENT_RECOVERED,
                {"entry_id": self.config_entry.entry_id, "down_seconds": down_for},
            )
            # A modem that dropped off the LAN probably rebooted; counters will
            # reset and be caught above.
        self._was_unreachable = False
        self.unreachable_since = None
