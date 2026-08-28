"""Ecovacs GOAT Open API integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import EcovacsGoatApiClient, EcovacsGoatApiError, normalize_base_url
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_NICKNAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from .coordinator import EcovacsGoatCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LAWN_MOWER, Platform.BINARY_SENSOR, Platform.SENSOR]

SERVICE_START_MOWING = "start_mowing"
SERVICE_PAUSE_MOWING = "pause_mowing"
SERVICE_RETURN_TO_BASE = "return_to_base"
SERVICE_REFRESH = "refresh"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ecovacs GOAT Open API from a config entry."""
    session = async_get_clientsession(hass)
    api = EcovacsGoatApiClient(
        session=session,
        api_key=entry.data[CONF_API_KEY],
        base_url=normalize_base_url(entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)),
    )
    coordinator = EcovacsGoatCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            for service in (
                SERVICE_START_MOWING,
                SERVICE_PAUSE_MOWING,
                SERVICE_RETURN_TO_BASE,
                SERVICE_REFRESH,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    async def _get_api_and_coordinator(call: ServiceCall) -> tuple[EcovacsGoatApiClient, EcovacsGoatCoordinator, str]:
        entry_id = call.data.get("config_entry_id")
        domain_data = hass.data.get(DOMAIN, {})
        if not entry_id:
            if len(domain_data) != 1:
                raise vol.Invalid("config_entry_id is required when multiple entries exist")
            entry_id = next(iter(domain_data))
        if entry_id not in domain_data:
            raise vol.Invalid(f"Unknown config_entry_id: {entry_id}")
        api = domain_data[entry_id]["api"]
        coordinator = domain_data[entry_id]["coordinator"]
        nickname = call.data.get(CONF_NICKNAME) or coordinator.nickname
        return api, coordinator, nickname

    async def async_handle_start_mowing(call: ServiceCall) -> None:
        api, coordinator, nickname = await _get_api_and_coordinator(call)
        work_state = (coordinator.data or {}).get("work_state") or {}
        resume = str(work_state.get("cleanSt", "")).lower() == "p" if isinstance(work_state, dict) else False
        await api.async_start_mowing(nickname, resume=resume)
        coordinator.async_set_local_activity("mowing", seconds=90)
        await coordinator.async_request_refresh()

    async def async_handle_refresh(call: ServiceCall) -> None:
        _, coordinator, _ = await _get_api_and_coordinator(call)
        await coordinator.async_request_refresh()

    async def async_handle_return_to_base(call: ServiceCall) -> None:
        api, coordinator, nickname = await _get_api_and_coordinator(call)
        try:
            await api.async_return_to_base(nickname)
        except EcovacsGoatApiError as err:
            raise HomeAssistantError(f"Ecovacs GOAT konnte nicht zur Station geschickt werden: {err}") from err
        await coordinator.async_request_refresh()

    async def async_handle_pause_mowing(call: ServiceCall) -> None:
        api, coordinator, nickname = await _get_api_and_coordinator(call)
        try:
            await api.async_pause_mowing(nickname)
        except EcovacsGoatApiError as err:
            raise HomeAssistantError(f"Ecovacs GOAT konnte nicht pausiert werden: {err}") from err
        coordinator.async_set_local_activity(None)
        await coordinator.async_request_refresh()

    command_schema = vol.Schema(
        {
            vol.Optional("config_entry_id"): cv.string,
            vol.Optional(CONF_NICKNAME): cv.string,
        }
    )
    services = (
        (SERVICE_START_MOWING, async_handle_start_mowing, command_schema),
        (SERVICE_PAUSE_MOWING, async_handle_pause_mowing, command_schema),
        (SERVICE_RETURN_TO_BASE, async_handle_return_to_base, command_schema),
        (
            SERVICE_REFRESH,
            async_handle_refresh,
            vol.Schema({vol.Optional("config_entry_id"): cv.string}),
        ),
    )
    for service, handler, schema in services:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)
