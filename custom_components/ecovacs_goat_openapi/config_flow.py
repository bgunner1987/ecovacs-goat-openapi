"""Config flow for Ecovacs GOAT Open API."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    EcovacsGoatApiClient,
    EcovacsGoatApiError,
    EcovacsGoatAuthError,
    extract_device_id,
    extract_device_nickname,
    normalize_base_url,
)
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_NICKNAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.config_entries import OptionsFlowWithReload as _OptionsFlowBase
except ImportError:  # pragma: no cover - compatibility for older HA cores
    _OptionsFlowBase = config_entries.OptionsFlow


class EcovacsGoatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ecovacs GOAT Open API."""

    VERSION = 1
    MINOR_VERSION = 6

    def __init__(self) -> None:
        """Initialize flow."""
        self._user_input: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_input = {
                **user_input,
                CONF_BASE_URL: normalize_base_url(user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)),
            }
            try:
                self._devices = await _async_get_devices(self.hass, self._user_input)
            except EcovacsGoatAuthError:
                errors[CONF_API_KEY] = "invalid_auth"
            except EcovacsGoatApiError as err:
                _LOGGER.exception("Ecovacs Open API validation failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                choices = _device_choices(self._devices)
                if not choices:
                    errors["base"] = "no_devices"
                elif len(choices) == 1:
                    nickname = next(iter(choices))
                    await self.async_set_unique_id(nickname)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=nickname,
                        data={**self._user_input, CONF_NICKNAME: nickname},
                    )
                else:
                    return await self.async_step_select_device()

        return self.async_show_form(
            step_id="user",
            data_schema=_setup_schema(),
            errors=errors,
        )

    async def async_step_select_device(self, user_input: dict[str, Any] | None = None):
        """Let the user select one device if the API key exposes multiple devices."""
        choices = _device_choices(self._devices)

        if user_input is not None:
            nickname = user_input[CONF_NICKNAME]
            await self.async_set_unique_id(nickname)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=nickname,
                data={**self._user_input, CONF_NICKNAME: nickname},
            )

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required(CONF_NICKNAME): vol.In(choices)}),
            errors={},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Reconfigure API key, host, nickname or interval without deleting the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **entry.data,
                **user_input,
                CONF_BASE_URL: normalize_base_url(user_input.get(CONF_BASE_URL, entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL))),
            }
            # Keep the same unique id/device, unless the user explicitly changed the nickname.
            nickname = data.get(CONF_NICKNAME, entry.data.get(CONF_NICKNAME, entry.title))
            data[CONF_NICKNAME] = nickname
            try:
                devices = await _async_get_devices(self.hass, data)
            except EcovacsGoatAuthError:
                errors[CONF_API_KEY] = "invalid_auth"
            except EcovacsGoatApiError as err:
                _LOGGER.exception("Ecovacs Open API reconfigure validation failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                available = _device_choices(devices)
                if available and nickname not in available:
                    errors[CONF_NICKNAME] = "device_not_found"
                else:
                    await self.async_set_unique_id(entry.unique_id or nickname)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates=data,
                        options={CONF_SCAN_INTERVAL: data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)},
                    )

        current = {**entry.data, **entry.options}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(current),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return EcovacsGoatOptionsFlow()


class EcovacsGoatOptionsFlow(_OptionsFlowBase):
    """Handle options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=15, max=3600)
                    )
                }
            ),
        )


def _setup_schema() -> vol.Schema:
    """Return setup schema."""
    return vol.Schema(
        {
            vol.Required(CONF_API_KEY): str,
            vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SECONDS): vol.All(
                vol.Coerce(int), vol.Range(min=15, max=3600)
            ),
        }
    )


def _reconfigure_schema(current: dict[str, Any]) -> vol.Schema:
    """Return reconfigure schema."""
    return vol.Schema(
        {
            vol.Required(CONF_API_KEY, default=current.get(CONF_API_KEY, "")): str,
            vol.Optional(CONF_BASE_URL, default=current.get(CONF_BASE_URL, DEFAULT_BASE_URL)): str,
            vol.Required(CONF_NICKNAME, default=current.get(CONF_NICKNAME, "")): str,
            vol.Optional(CONF_SCAN_INTERVAL, default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)): vol.All(
                vol.Coerce(int), vol.Range(min=15, max=3600)
            ),
        }
    )


def _device_choices(devices: list[dict[str, Any]]) -> dict[str, str]:
    """Return device choices keyed by nickname."""
    choices: dict[str, str] = {}
    for device in devices:
        nickname = extract_device_nickname(device)
        if not nickname:
            continue
        device_id = extract_device_id(device, nickname)
        choices[nickname] = f"{nickname} ({device_id})"
    return choices


async def _async_get_devices(hass: HomeAssistant, user_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate credentials and return devices."""
    api = EcovacsGoatApiClient(
        session=async_get_clientsession(hass),
        api_key=user_input[CONF_API_KEY],
        base_url=user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )
    return await api.async_get_device_list()
