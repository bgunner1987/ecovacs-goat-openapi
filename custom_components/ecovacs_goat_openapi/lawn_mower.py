"""Lawn mower entity for Ecovacs GOAT Open API."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_CHARGE_STATE, ATTR_CLEAN_STATE, ATTR_ERROR_REASON, ATTR_STATION_STATE, DOMAIN
from .coordinator import EcovacsGoatCoordinator
from .entity import EcovacsGoatEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up lawn mower entity."""
    coordinator: EcovacsGoatCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([EcovacsGoatLawnMower(coordinator)])


class EcovacsGoatLawnMower(EcovacsGoatEntity, LawnMowerEntity):
    """Simple GOAT mower entity: start only, no pause/dock exposure."""

    _attr_supported_features = LawnMowerEntityFeature.START_MOWING

    def __init__(self, coordinator: EcovacsGoatCoordinator) -> None:
        """Initialize the mower entity."""
        super().__init__(coordinator)
        self._attr_name = None
        self._attr_unique_id = f"{DOMAIN}_{self._nickname}_mower"

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return current mower activity from cached coordinator data."""
        data = self.coordinator.data or {}
        if data.get("has_error"):
            return LawnMowerActivity.ERROR

        if _as_lower(data.get("local_activity_override")) == "mowing":
            return LawnMowerActivity.MOWING

        work_state = self._work_state
        clean_state = _as_lower(work_state.get("cleanSt"))
        charge_state = _as_lower(work_state.get("chargeSt"))

        if clean_state in {"s", "r", "goposition", "findpet", "cruise", "buildmap"}:
            return LawnMowerActivity.MOWING
        if clean_state in {"p", "gopositionpause", "findpetpause", "cruisepause", "buildmappause"}:
            return LawnMowerActivity.PAUSED
        if charge_state in {"g", "gp", "go", "go-start", "returning"}:
            return LawnMowerActivity.RETURNING
        if clean_state == "h" or charge_state in {"sc", "wc", "charging", "charge", "docked"}:
            return LawnMowerActivity.DOCKED
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose raw compact state codes for automations/debugging."""
        attrs = super().extra_state_attributes
        work_state = self._work_state
        data = self.coordinator.data or {}
        attrs.update(
            {
                ATTR_CLEAN_STATE: work_state.get("cleanSt"),
                ATTR_CHARGE_STATE: work_state.get("chargeSt"),
                ATTR_STATION_STATE: work_state.get("stationSt"),
                "has_error": data.get("has_error"),
                ATTR_ERROR_REASON: data.get(ATTR_ERROR_REASON),
            }
        )
        return attrs

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        resume = _as_lower(self._work_state.get("cleanSt")) == "p"
        await self.coordinator.api.async_start_mowing(self._nickname, resume=resume)
        self.coordinator.async_set_local_activity("mowing", seconds=90)
        await self.coordinator.async_request_refresh()

    @property
    def _work_state(self) -> dict[str, Any]:
        """Return current work-state dictionary."""
        data = self.coordinator.data or {}
        work_state = data.get("work_state")
        return work_state if isinstance(work_state, dict) else {}


def _as_lower(value: Any) -> str:
    """Return a lowercase string value."""
    return str(value).lower() if value is not None else ""
