"""Ubee Cable Modem integration for Home Assistant."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import UbeeModemClient
from .const import (
    CONF_STATUS_PATH,
    CONF_USE_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATUS_PATH,
)
from .coordinator import UbeeCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

type UbeeConfigEntry = ConfigEntry[UbeeCoordinator]


def build_client(hass: HomeAssistant, data: dict) -> UbeeModemClient:
    """Create a client with its own cookie jar (IP-address hosts need unsafe=True)."""
    session = async_create_clientsession(
        hass,
        verify_ssl=False,
        cookie_jar=aiohttp.CookieJar(unsafe=True),
    )
    return UbeeModemClient(
        session,
        data[CONF_HOST],
        data.get(CONF_STATUS_PATH, DEFAULT_STATUS_PATH),
        data.get(CONF_USERNAME),
        data.get(CONF_PASSWORD),
        data.get(CONF_USE_SSL, False),
    )


async def async_setup_entry(hass: HomeAssistant, entry: UbeeConfigEntry) -> bool:
    """Set up a modem from a config entry."""
    client = build_client(hass, dict(entry.data))
    scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = UbeeCoordinator(hass, entry, client, scan)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: UbeeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UbeeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
