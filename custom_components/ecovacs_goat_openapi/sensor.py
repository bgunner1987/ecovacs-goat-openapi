"""Sensors for Ecovacs GOAT Open API."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_ERROR_DETAILS, ATTR_ERROR_REASON, ATTR_LAST_RESPONSE, DOMAIN
from .coordinator import EcovacsGoatCoordinator
from .entity import EcovacsGoatEntity


def _work_state(data: dict[str, Any]) -> dict[str, Any]:
    """Return work-state dictionary from coordinator data."""
    work_state = data.get("work_state")
    return work_state if isinstance(work_state, dict) else {}


CLEAN_STATE_LABELS = {
    "s": "Mähen",
    "r": "Mähen fortsetzen",
    "p": "Pausiert",
    "h": "Inaktiv / beendet",
    "goposition": "Fährt zu Position",
    "gopositionpause": "Positionsfahrt pausiert",
    "findpet": "Haustier suchen",
    "findpetpause": "Haustiersuche pausiert",
    "cruise": "Patrouille",
    "cruisepause": "Patrouille pausiert",
    "buildmap": "Karte erstellen",
    "buildmappause": "Kartierung pausiert",
}

CHARGE_STATE_LABELS = {
    "g": "Rückkehr zur Basis",
    "gp": "Rückkehr pausiert",
    "i": "Inaktiv",
    "sc": "Lädt in Station",
    "wc": "Lädt per Kabel",
    "charging": "Lädt",
}


def _labelled_code(work_state_key: str, labels: dict[str, str]) -> Callable[[dict[str, Any]], Any]:
    """Return a readable label while keeping the raw Ecovacs code visible."""
    def _value(data: dict[str, Any]) -> Any:
        value = _work_state(data).get(work_state_key)
        if value is None:
            return "Nicht gemeldet"
        code = str(value)
        label = labels.get(code.lower())
        return f"{label} ({code})" if label else code

    return _value


def _json_compact(value: Any, limit: int = 3000) -> str:
    """Return a compact JSON string, clipped to avoid huge state attributes."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


@dataclass(frozen=True, kw_only=True)
class EcovacsGoatSensorEntityDescription(SensorEntityDescription):
    """Description of an Ecovacs GOAT sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[EcovacsGoatSensorEntityDescription, ...] = (
    EcovacsGoatSensorEntityDescription(
        key="error_reason",
        translation_key="error_reason",
        value_fn=lambda data: data.get(ATTR_ERROR_REASON, "OK"),
    ),
    EcovacsGoatSensorEntityDescription(
        key="mowing_state",
        translation_key="mowing_state",
        value_fn=_labelled_code("cleanSt", CLEAN_STATE_LABELS),
    ),
    EcovacsGoatSensorEntityDescription(
        key="charging_state",
        translation_key="charging_state",
        value_fn=_labelled_code("chargeSt", CHARGE_STATE_LABELS),
    ),
    EcovacsGoatSensorEntityDescription(
        key="api_status",
        translation_key="api_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("api_status", "unknown"),
    ),
    EcovacsGoatSensorEntityDescription(
        key="raw_work_state",
        translation_key="raw_work_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: "available" if _work_state(data) else "no_work_state",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator: EcovacsGoatCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([EcovacsGoatSensor(coordinator, description) for description in SENSORS])


class EcovacsGoatSensor(EcovacsGoatEntity, SensorEntity):
    """Representation of an Ecovacs GOAT sensor."""

    entity_description: EcovacsGoatSensorEntityDescription

    def __init__(
        self,
        coordinator: EcovacsGoatCoordinator,
        description: EcovacsGoatSensorEntityDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{self._nickname}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return native sensor value."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return sensor attributes."""
        attrs = super().extra_state_attributes
        data = self.coordinator.data or {}
        if self.entity_description.key in {"error_reason", "raw_work_state", "api_status"}:
            attrs[ATTR_ERROR_DETAILS] = data.get(ATTR_ERROR_DETAILS)
            attrs["query_variant"] = data.get("query_variant")
        if self.entity_description.key == "raw_work_state":
            attrs["work_state_json"] = _json_compact(_work_state(data))
            attrs[ATTR_LAST_RESPONSE] = _json_compact(data.get(ATTR_LAST_RESPONSE))
        return attrs
