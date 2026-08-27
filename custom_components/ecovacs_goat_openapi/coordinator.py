"""Data coordinator for the Ecovacs GOAT Open API integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EcovacsGoatApiClient, EcovacsGoatApiError, extract_error_info, extract_work_state
from .const import (
    ATTR_ERROR_DETAILS,
    ATTR_ERROR_REASON,
    ATTR_LAST_RESPONSE,
    CONF_NICKNAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EcovacsGoatCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep the GOAT mower state in memory for entities."""

    def __init__(self, hass: HomeAssistant, api: EcovacsGoatApiClient, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.entry = entry
        self.nickname: str = entry.data[CONF_NICKNAME]
        self._local_activity_override: str | None = None
        self._local_activity_override_until: datetime | None = None
        seconds = int(entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.nickname}",
            update_interval=timedelta(seconds=max(15, seconds)),
        )

    def async_set_local_activity(self, activity: str | None, *, seconds: int = 90) -> None:
        """Temporarily prefer a command-confirmed local activity over stale cloud state."""
        if activity is None:
            self._local_activity_override = None
            self._local_activity_override_until = None
            return
        self._local_activity_override = activity
        self._local_activity_override_until = datetime.now(timezone.utc) + timedelta(seconds=max(15, seconds))

    def _current_local_activity_override(self) -> str | None:
        """Return active local override or clear it if expired."""
        if self._local_activity_override_until is None:
            return None
        if datetime.now(timezone.utc) >= self._local_activity_override_until:
            self.async_set_local_activity(None)
            return None
        return self._local_activity_override

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Ecovacs."""
        try:
            response = await self.api.async_get_work_state(self.nickname)
        except EcovacsGoatApiError as err:
            raise UpdateFailed(str(err)) from err

        work_state = extract_work_state(response)
        error_info = extract_error_info(response, work_state)
        code = response.get("code", response.get("status"))
        message = response.get("msg", response.get("message"))
        query_variant = response.get("_ha_work_state_query")
        attempts = response.get("_ha_work_state_attempts", [])

        clean_state = str(work_state.get("cleanSt", "")).lower()
        override = self._current_local_activity_override()
        if override == "mowing" and clean_state in {"s", "r"}:
            self.async_set_local_activity(None)
            override = None

        if not any(key in work_state for key in ("cleanSt", "chargeSt", "stationSt")):
            _LOGGER.warning(
                "Ecovacs GOAT work-state response for %s did not contain cleanSt/chargeSt/stationSt. "
                "code=%s message=%s query_variant=%s attempts=%s extracted_work_state=%s raw_response=%s",
                self.nickname,
                code,
                message,
                query_variant,
                attempts,
                work_state,
                response,
            )

        return {
            "work_state": work_state,
            "api_code": code,
            "api_message": message,
            "api_status": "ok" if code in (0, "0", None) else "api_error",
            "query_variant": query_variant,
            "query_attempts": attempts,
            "local_activity_override": override,
            "has_error": error_info.is_error,
            ATTR_ERROR_REASON: error_info.reason,
            ATTR_ERROR_DETAILS: error_info.details,
            ATTR_LAST_RESPONSE: response,
        }
