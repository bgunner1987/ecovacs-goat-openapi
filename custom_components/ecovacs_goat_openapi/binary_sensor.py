"""Binary sensors for Ecovacs GOAT Open API."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_ERROR_DETAILS, ATTR_ERROR_REASON, DOMAIN
from .coordinator import EcovacsGoatCoordinator
from .entity import EcovacsGoatEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator: EcovacsGoatCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([EcovacsGoatErrorBinarySensor(coordinator)])


class EcovacsGoatErrorBinarySensor(EcovacsGoatEntity, BinarySensorEntity):
    """Problem sensor for the mower."""

    _attr_translation_key = "error"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:robot-mower-alert"

    def __init__(self, coordinator: EcovacsGoatCoordinator) -> None:
        """Initialize the problem sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{self._nickname}_error"

    @property
    def is_on(self) -> bool:
        """Return true when the API payload indicates a problem."""
        return bool((self.coordinator.data or {}).get("has_error"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details useful for a dashboard/debugging."""
        attrs = super().extra_state_attributes
        data = self.coordinator.data or {}
        attrs[ATTR_ERROR_REASON] = data.get(ATTR_ERROR_REASON, "OK")
        details = data.get(ATTR_ERROR_DETAILS)
        if details:
            attrs[ATTR_ERROR_DETAILS] = details
        return attrs
