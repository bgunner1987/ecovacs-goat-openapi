"""Diagnostics support for Ecovacs GOAT Open API."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_API_KEY, DOMAIN

TO_REDACT = {CONF_API_KEY, "ak", "token", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    return {
        "entry": {
            "data": _redact(dict(entry.data)),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "last_data": _redact(coordinator.data or {}),
    }


def _redact(value: Any) -> Any:
    """Recursively redact sensitive values."""
    if isinstance(value, dict):
        return {key: "***REDACTED***" if key in TO_REDACT else _redact(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
