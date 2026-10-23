"""Diagnostics for the Ubee Cable Modem integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import UbeeConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "ipv4", "ipv6"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UbeeConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    data = c.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "auth_method": c.client.auth_method,
        "unreachable_count": c.unreachable_count,
        "last_unreachable": c.last_unreachable.isoformat() if c.last_unreachable else None,
        "status": async_redact_data(data.status.as_dict(), TO_REDACT) if data else None,
        "derived": {
            "total_corrected": data.total_corrected,
            "total_uncorrectable": data.total_uncorrectable,
            "corrected_per_min": data.corrected_per_min,
            "uncorrectable_per_min": data.uncorrectable_per_min,
            "channel_uncorrectable_delta": data.channel_uncorrectable_delta,
        }
        if data
        else None,
        # Raw page helps support other Ubee firmware variants.
        "last_html": c.client.last_html,
    }
