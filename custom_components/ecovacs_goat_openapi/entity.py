"""Base entities for Ecovacs GOAT Open API."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BASE_URL, CONF_NICKNAME, DOMAIN
from .coordinator import EcovacsGoatCoordinator


class EcovacsGoatEntity(CoordinatorEntity[EcovacsGoatCoordinator]):
    """Base Ecovacs GOAT entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EcovacsGoatCoordinator) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self._nickname = coordinator.entry.data[CONF_NICKNAME]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._nickname)},
            manufacturer="Ecovacs",
            name=self._nickname,
            model="GOAT A1600 RTK",
            configuration_url="https://open.ecovacs.com/",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return common extra attributes."""
        return {
            "nickname": self._nickname,
            "api_host": self.coordinator.entry.data.get(CONF_BASE_URL),
        }
