"""Config flow for the Ubee Cable Modem integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import build_client
from .api import UbeeAuthError, UbeeConnectionError, UbeeParseError
from .const import (
    CONF_STATUS_PATH,
    CONF_UNCORRECTABLE_THRESHOLD,
    CONF_USE_SSL,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATUS_PATH,
    DEFAULT_UNCORRECTABLE_THRESHOLD,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
            vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): _PASSWORD_SELECTOR,
            vol.Required(
                CONF_STATUS_PATH, default=defaults.get(CONF_STATUS_PATH, DEFAULT_STATUS_PATH)
            ): str,
            vol.Required(CONF_USE_SSL, default=defaults.get(CONF_USE_SSL, False)): bool,
        }
    )


async def _validate(hass, data: dict[str, Any]) -> dict[str, str]:
    """Return an errors dict (empty on success)."""
    client = build_client(hass, data)
    try:
        status = await client.async_get_status()
    except UbeeAuthError:
        return {"base": "invalid_auth"}
    except UbeeConnectionError:
        return {"base": "cannot_connect"}
    except UbeeParseError:
        return {"base": "unexpected_page"}
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating Ubee modem")
        return {"base": "unknown"}
    _LOGGER.debug(
        "Validated Ubee modem: %d DS / %d US channels, auth=%s",
        len(status.downstream),
        len(status.upstream),
        client.auth_method,
    )
    return {}


class UbeeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Ubee modem."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST].strip().lower())
            self._abort_if_unique_id_configured()
            errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"Ubee modem ({user_input[CONF_HOST]})", data=user_input
                )
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input or {}), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors = await _validate(self.hass, data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                    vol.Optional(CONF_PASSWORD, default=""): _PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UbeeOptionsFlow()


class UbeeOptionsFlow(OptionsFlow):
    """Polling interval and alert threshold."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                    vol.Required(
                        CONF_UNCORRECTABLE_THRESHOLD,
                        default=opts.get(
                            CONF_UNCORRECTABLE_THRESHOLD, DEFAULT_UNCORRECTABLE_THRESHOLD
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                }
            ),
        )
