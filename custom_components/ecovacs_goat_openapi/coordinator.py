"""Data coordinator for the Ecovacs GOAT Open API integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EcovacsGoatApiClient,
    EcovacsGoatApiError,
    EcovacsGoatAuthError,
    EcovacsGoatTransientError,
    extract_error_info,
    extract_work_state,
)
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

_WORK_STATE_KEYS = ("cleanSt", "chargeSt", "stationSt")
_TEMPORARY_WORK_STATE_ERRORS = {
    4200: "offline",
    10004: "communication_timeout",
}

_WorkStateErrorSignature = tuple[int, str]


class EcovacsGoatCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep the GOAT mower state in memory for entities."""

    def __init__(self, hass: HomeAssistant, api: EcovacsGoatApiClient, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.entry = entry
        self.nickname: str = entry.data[CONF_NICKNAME]
        self._local_activity_override: str | None = None
        self._local_activity_override_until: datetime | None = None
        self._last_work_state_error: _WorkStateErrorSignature | None = None
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
        except EcovacsGoatAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EcovacsGoatTransientError as err:
            if self.data and isinstance(self.data.get("work_state"), dict):
                _LOGGER.debug(
                    "Transient Ecovacs GOAT work-state failure for %s (%s); cached state retained",
                    self.nickname,
                    type(err).__name__,
                )
                return self.data
            raise UpdateFailed(str(err)) from err
        except EcovacsGoatApiError as err:
            raise UpdateFailed(str(err)) from err

        work_state = extract_work_state(response)
        code = response.get("code", response.get("status"))
        message = response.get("msg", response.get("message"))
        query_variant = response.get("_ha_work_state_query")
        attempts = response.get("_ha_work_state_attempts", [])

        cached_state_available = bool(
            self.data and isinstance(self.data.get("work_state"), dict)
        )
        temporary_error = _temporary_work_state_error(work_state)
        if temporary_error is not None:
            errno, error_kind = temporary_error
            signature: _WorkStateErrorSignature = (errno, error_kind)
            previous_error = self._last_work_state_error
            if signature != previous_error:
                # Without cached data, DataUpdateCoordinator reports the
                # UpdateFailed transition. Avoid logging that first failure twice.
                if cached_state_available or previous_error is not None:
                    if error_kind == "offline":
                        _LOGGER.warning(
                            "Ecovacs GOAT %s is offline (errno=%s)",
                            self.nickname,
                            errno,
                        )
                    else:
                        _LOGGER.warning(
                            "Ecovacs GOAT %s communication timeout (errno=%s)",
                            self.nickname,
                            errno,
                        )
            else:
                _LOGGER.debug(
                    "Ecovacs GOAT %s remains unavailable (%s, errno=%s)",
                    self.nickname,
                    error_kind,
                    errno,
                )
            self._last_work_state_error = signature
            _LOGGER.debug(
                "Ecovacs GOAT temporary work-state failure for %s: errno=%s "
                "variant=%s code=%s response_type=%s available_keys=%s attempts=%s "
                "cached_state_retained=%s",
                self.nickname,
                errno,
                query_variant,
                code,
                type(response.get("data")).__name__,
                sorted(work_state.keys()),
                attempts,
                cached_state_available,
            )
            if cached_state_available:
                return self.data
            raise UpdateFailed(
                f"Ecovacs GOAT temporarily unavailable ({error_kind}, errno={errno})"
            )

        clean_state = str(work_state.get("cleanSt", "")).lower()
        override = self._current_local_activity_override()
        if override == "mowing" and clean_state in {"s", "r"}:
            self.async_set_local_activity(None)
            override = None

        if not any(key in work_state for key in _WORK_STATE_KEYS):
            # Unknown failures and true schema changes must remain visible.
            # They also break a known temporary-failure episode without being
            # reported as a successful recovery.
            self._last_work_state_error = None
            _LOGGER.warning(
                "Ecovacs GOAT work-state response for %s did not contain "
                "cleanSt/chargeSt/stationSt: ret=%s errno=%s code=%s variant=%s "
                "response_type=%s available_keys=%s",
                self.nickname,
                work_state.get("ret"),
                _normalized_errno(work_state.get("errno")),
                code,
                query_variant,
                type(response.get("data")).__name__,
                sorted(work_state.keys()),
            )
            if cached_state_available:
                _LOGGER.debug(
                    "Invalid Ecovacs GOAT work-state for %s: variant=%s code=%s response_type=%s "
                    "available_keys=%s attempts=%s; cached state retained",
                    self.nickname,
                    query_variant,
                    code,
                    type(response.get("data")).__name__,
                    sorted(work_state.keys()),
                    attempts,
                )
                return self.data
            raise UpdateFailed("Ecovacs GOAT response did not contain a valid work state")

        if self._last_work_state_error is not None:
            _LOGGER.info("Ecovacs GOAT %s is reachable again", self.nickname)
            self._last_work_state_error = None

        error_info = extract_error_info(response, work_state)
        _LOGGER.debug(
            "Valid Ecovacs GOAT work-state for %s: variant=%s code=%s available_keys=%s",
            self.nickname,
            query_variant,
            code,
            sorted(key for key in work_state if key in _WORK_STATE_KEYS),
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


def _temporary_work_state_error(work_state: dict[str, Any]) -> tuple[int, str] | None:
    """Return a known temporary mower communication failure, if present."""
    if str(work_state.get("ret", "")).strip().lower() != "fail":
        return None
    errno = _normalized_errno(work_state.get("errno"))
    if errno is None or errno not in _TEMPORARY_WORK_STATE_ERRORS:
        return None
    return errno, _TEMPORARY_WORK_STATE_ERRORS[errno]


def _normalized_errno(value: Any) -> int | None:
    """Normalize Ecovacs errno values that may arrive as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
