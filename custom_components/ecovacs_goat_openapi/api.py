"""Client and payload helpers for the Ecovacs Open API / MCP robot endpoints."""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import API_TIMEOUT_SECONDS, ENDPOINT_ROBOT_CTL, ENDPOINT_ROBOT_DEVICE_LIST

_LOGGER = logging.getLogger(__name__)


class EcovacsGoatApiError(Exception):
    """Raised when the Ecovacs Open API returns an error."""


class EcovacsGoatAuthError(EcovacsGoatApiError):
    """Raised when authentication fails."""


class EcovacsGoatTransientError(EcovacsGoatApiError):
    """Raised for a temporary network or upstream-server failure."""


@dataclass(frozen=True)
class ErrorInfo:
    """Compact error/problemdetection result."""

    is_error: bool
    reason: str
    details: dict[str, Any]


def normalize_base_url(base_url: str) -> str:
    """Normalize portal/API URLs to the bare API host."""
    raw = (base_url or "").strip()
    if not raw:
        raw = "https://open.ecovacs.com"
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"

    parsed = urlsplit(raw)
    if not parsed.netloc:
        raise EcovacsGoatApiError("Ungültiger Ecovacs API-Host")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def normalize_api_key(api_key: str) -> str:
    """Extract a raw AK/API key from common pasted formats."""
    raw = (api_key or "").strip().strip('"').strip("'")
    if not raw:
        return ""

    if raw.startswith(("http://", "https://")):
        parsed = urlsplit(raw)
        query = parse_qs(parsed.query)
        for key in ("ak", "ECO_API_KEY", "api_key"):
            if query.get(key):
                return query[key][0].strip().strip('"').strip("'")

    for prefix in ("ECO_API_KEY=", "API_KEY=", "ak=", "api_key="):
        if raw.startswith(prefix):
            return raw[len(prefix) :].strip().strip('"').strip("'")

    for marker in ('"ECO_API_KEY"', "'ECO_API_KEY'", '"ak"', "'ak'", '"api_key"', "'api_key'"):
        if marker in raw and ":" in raw:
            after = raw.split(marker, 1)[1].split(":", 1)[1].lstrip()
            quote = after[:1]
            if quote in {'"', "'"}:
                return after[1:].split(quote, 1)[0].strip()

    return raw


class EcovacsGoatApiClient:
    """Small async client for the Ecovacs Open API robot endpoints."""

    def __init__(self, session: ClientSession, api_key: str, base_url: str) -> None:
        """Initialize the API client."""
        self._session = session
        self._api_key = normalize_api_key(api_key)
        self._base_url = normalize_base_url(base_url)
        self._preferred_work_state_variant: str | None = None

    @property
    def base_url(self) -> str:
        """Return configured API base URL."""
        return self._base_url

    async def async_get_device_list(self) -> list[dict[str, Any]]:
        """Return all devices bound to the API key."""
        response = await self._request(ENDPOINT_ROBOT_DEVICE_LIST, {}, method="get")
        data = response.get("data")
        if not isinstance(data, list):
            return []

        devices: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, str) and item.strip():
                nickname = item.strip()
                devices.append(
                    {
                        "nickname": nickname,
                        "nickName": nickname,
                        "name": nickname,
                        "deviceId": nickname,
                        "source_shape": "nickname_string",
                    }
                )
            elif isinstance(item, dict):
                nickname = extract_device_nickname(item)
                if nickname:
                    normalized = dict(item)
                    normalized.setdefault("nickname", nickname)
                    normalized.setdefault("nickName", nickname)
                    normalized.setdefault("name", nickname)
                    devices.append(normalized)
        return devices

    async def async_get_work_state(self, nickname: str) -> dict[str, Any]:
        """Return raw working status.

        The official MCP example uses POST with `nickName`. Read-only fallback
        variants are kept because the API has been inconsistent between regions
        and product families. No movement/stop/dock command is sent here.
        """
        variants: tuple[tuple[str, str, dict[str, str]], ...] = (
            ("post_nickName", "post", {"nickName": nickname, "cmd": "GetWorkState", "act": ""}),
            ("post_nickname", "post", {"nickname": nickname, "cmd": "GetWorkState", "act": ""}),
            ("get_nickName", "get", {"nickName": nickname, "cmd": "GetWorkState", "act": ""}),
            ("get_nickname", "get", {"nickname": nickname, "cmd": "GetWorkState", "act": ""}),
        )
        if self._preferred_work_state_variant:
            variants = tuple(
                sorted(variants, key=lambda item: item[0] != self._preferred_work_state_variant)
            )
        attempts: list[dict[str, Any]] = []
        last_response: dict[str, Any] | None = None
        last_error: Exception | None = None

        for label, method, payload in variants:
            try:
                response = await self._request(
                    ENDPOINT_ROBOT_CTL,
                    payload,
                    method=method,
                    raise_api_error=False,
                    retry_transient=True,
                )
                last_response = response
                work_state = extract_work_state(response)
                attempts.append(_summarize_attempt(label, response, work_state))
                if _has_work_state_values(work_state):
                    self._preferred_work_state_variant = label
                    _LOGGER.debug("Ecovacs GOAT work-state variant succeeded: %s", label)
                    response = dict(response)
                    response["_ha_work_state_query"] = label
                    response["_ha_work_state_attempts"] = attempts
                    return response
            except EcovacsGoatAuthError:
                raise
            except EcovacsGoatApiError as err:
                last_error = err
                attempts.append({"variant": label, "error_type": type(err).__name__})

        if last_response is not None:
            response = dict(last_response)
            response["_ha_work_state_query"] = "no_variant_returned_work_state"
            response["_ha_work_state_attempts"] = attempts
            return response

        if last_error is not None:
            raise EcovacsGoatApiError(f"GetWorkState fehlgeschlagen: {last_error}") from last_error
        raise EcovacsGoatApiError("GetWorkState fehlgeschlagen: keine Antwort")

    async def async_start_mowing(self, nickname: str, *, resume: bool = False) -> dict[str, Any]:
        """Start or resume mowing via the official Clean command."""
        return await self._request(
            ENDPOINT_ROBOT_CTL,
            {"nickName": nickname, "cmd": "Clean", "act": "r" if resume else "s"},
            method="post",
        )

    async def async_return_to_base(self, nickname: str) -> dict[str, Any]:
        """Ask the mower to return to its charging station."""
        return await self._request(
            ENDPOINT_ROBOT_CTL,
            {"nickName": nickname, "cmd": "Charge", "act": "go-start"},
            method="post",
            retry_transient=True,
        )

    async def _request(
        self,
        endpoint: str,
        payload: dict[str, Any],
        method: str,
        *,
        raise_api_error: bool = True,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        """Call the API and normalize errors."""
        if not self._api_key:
            raise EcovacsGoatAuthError("API-Key ist leer")

        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        string_payload = {key: str(value) for key, value in payload.items()}
        safe_payload_for_log = {**string_payload, "ak": "***"}

        max_attempts = 2 if retry_transient else 1
        for attempt in range(max_attempts):
            cause: Exception | None = None
            try:
                _LOGGER.debug("Calling Ecovacs Open API %s %s payload=%s", method.upper(), url, safe_payload_for_log)
                if method == "get":
                    async with self._session.get(
                        url, params={**string_payload, "ak": self._api_key}, timeout=API_TIMEOUT_SECONDS
                    ) as response:
                        data = await self._read_json(response)
                else:
                    async with self._session.post(
                        url,
                        json={**string_payload, "ak": self._api_key},
                        headers={"Content-Type": "application/json"},
                        timeout=API_TIMEOUT_SECONDS,
                    ) as response:
                        data = await self._read_json(response)
                break
            except ClientResponseError as err:
                cause = err
                if err.status in (401, 403):
                    raise EcovacsGoatAuthError("Ecovacs Open API hat den API-Key abgelehnt") from err
                if err.status not in (502, 503, 504):
                    raise EcovacsGoatApiError(f"HTTP-Fehler von Ecovacs Open API: {err.status}") from err
                transient = EcovacsGoatTransientError(f"Temporärer HTTP-Fehler: {err.status}")
            except TimeoutError as err:
                cause = err
                transient = EcovacsGoatTransientError("Zeitüberschreitung beim Kontakt zur Ecovacs Open API")
            except ClientError as err:
                cause = err
                transient = EcovacsGoatTransientError("Temporärer Verbindungsfehler zur Ecovacs Open API")
            if attempt + 1 >= max_attempts:
                raise transient from cause
            await asyncio.sleep(0.25 * (attempt + 1))

        _LOGGER.debug("Ecovacs Open API response: %s", _redact_api_key(data))
        if raise_api_error:
            self._raise_for_api_error(data)
        return data

    async def _read_json(self, response) -> dict[str, Any]:  # noqa: ANN001
        """Read JSON and raise useful errors."""
        response.raise_for_status()
        try:
            data = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            text = await response.text()
            snippet = text[:300].replace("\n", " ").replace("\r", " ")
            _LOGGER.debug("Unexpected Ecovacs response from %s: %s", response.url, snippet)
            raise EcovacsGoatApiError(
                "Ecovacs Open API hat kein JSON geliefert. Prüfe, ob als Host nur https://open.ecovacs.com eingetragen ist."
            ) from err
        if not isinstance(data, dict):
            raise EcovacsGoatApiError("Ecovacs Open API lieferte eine unerwartete Antwort")
        return data

    @staticmethod
    def _raise_for_api_error(data: dict[str, Any]) -> None:
        """Raise an exception if the API response indicates an error."""
        code = data.get("code") if "code" in data else data.get("status", 0)
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = 0
        if code_int == 0:
            return
        message = str(data.get("msg") or data.get("message") or f"API error code {code}")
        lower = message.lower()
        if code_int in (401, 403) or any(
            word in lower for word in ("auth", "key", "permission", "forbidden", "unauthorized", "ak")
        ):
            raise EcovacsGoatAuthError(message)
        raise EcovacsGoatApiError(message)


def _redact_api_key(value: Any) -> Any:
    """Return a copy-ish object with API keys redacted for logging."""
    if isinstance(value, dict):
        return {k: ("***" if k in {"ak", "api_key"} else _redact_api_key(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_api_key(item) for item in value]
    return value


def _parse_json_if_string(value: Any) -> Any:
    """Parse nested JSON strings, otherwise return the value unchanged."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def extract_device_nickname(device: dict[str, Any]) -> str | None:
    """Extract the best available nickname from a device-list item."""
    for key in ("nickname", "nickName", "name", "deviceName", "botName"):
        value = device.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_device_id(device: dict[str, Any], fallback: str) -> str:
    """Extract a stable-ish device identifier from a device-list item."""
    for key in ("did", "deviceId", "id", "serial", "sn", "name"):
        value = device.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return fallback


WORK_STATE_CODE_KEYS = ("cleanSt", "chargeSt", "stationSt")
WORK_STATE_PRIORITY_KEYS = (
    "data",
    "ctl",
    "body",
    "result",
    "response",
    "payload",
    "content",
    "value",
    "state",
    "workState",
    "work_state",
)


def extract_work_state(response: dict[str, Any]) -> dict[str, Any]:
    """Extract nested work-state data from known Open-API response variants."""
    found = _find_work_state_with_codes(response)
    if found:
        return found

    ret_only = _find_ret_only_state(response)
    if ret_only:
        return ret_only

    data = _parse_json_if_string(response.get("data"))
    return data if isinstance(data, dict) else {}


def _find_work_state_with_codes(value: Any, depth: int = 0) -> dict[str, Any]:
    """Recursively find a payload that contains cleanSt/chargeSt/stationSt."""
    if depth > 8:
        return {}
    value = _parse_json_if_string(value)

    if isinstance(value, dict):
        if any(key in value for key in WORK_STATE_CODE_KEYS):
            return value
        for key in WORK_STATE_PRIORITY_KEYS:
            if key in value:
                found = _find_work_state_with_codes(value[key], depth + 1)
                if found:
                    return found
        for nested in value.values():
            found = _find_work_state_with_codes(nested, depth + 1)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_work_state_with_codes(item, depth + 1)
            if found:
                return found

    return {}


def _find_ret_only_state(value: Any, depth: int = 0) -> dict[str, Any]:
    """Find a `{ret: ...}` payload when Ecovacs omits the actual state codes."""
    if depth > 8:
        return {}
    value = _parse_json_if_string(value)

    if isinstance(value, dict):
        if "ret" in value:
            return value
        for key in WORK_STATE_PRIORITY_KEYS:
            if key in value:
                found = _find_ret_only_state(value[key], depth + 1)
                if found:
                    return found
        for nested in value.values():
            found = _find_ret_only_state(nested, depth + 1)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_ret_only_state(item, depth + 1)
            if found:
                return found

    return {}


def _has_work_state_values(work_state: dict[str, Any]) -> bool:
    """Return true if a dictionary contains actual mower state code values."""
    return any(key in work_state for key in WORK_STATE_CODE_KEYS)


def _summarize_attempt(label: str, response: dict[str, Any], work_state: dict[str, Any]) -> dict[str, Any]:
    """Create a compact, non-secret summary for diagnostics."""
    data = response.get("data")
    if isinstance(data, dict):
        data_shape = sorted(str(key) for key in data.keys())[:20]
    elif isinstance(data, list):
        data_shape = f"list[{len(data)}]"
    else:
        data_shape = type(data).__name__
    return {
        "variant": label,
        "code": response.get("code", response.get("status")),
        "msg": response.get("msg", response.get("message")),
        "data_shape": data_shape,
        "work_state_keys": sorted(work_state.keys())[:20] if isinstance(work_state, dict) else [],
    }


_ERROR_KEY_WORDS = (
    "error",
    "err",
    "fault",
    "alarm",
    "warn",
    "warning",
    "abnormal",
    "exception",
    "stuck",
    "blocked",
    "block",
    "trap",
    "trapped",
    "help",
    "lift",
    "tilt",
)
_ERROR_VALUE_WORDS = (
    "error",
    "err",
    "fault",
    "alarm",
    "warn",
    "abnormal",
    "exception",
    "stuck",
    "blocked",
    "trapped",
    "help",
    "lifted",
    "tilt",
    "fail",
    "failed",
)
_OK_STRINGS = {
    "",
    "0",
    "0.0",
    "false",
    "none",
    "null",
    "normal",
    "ok",
    "success",
    "available",
    "[]",
    "{}",
    "no_error",
    "no error",
    "noerror",
}


def extract_error_info(response: dict[str, Any], work_state: dict[str, Any] | None = None) -> ErrorInfo:
    """Best-effort problem detection for dashboard use.

    Ecovacs' public MCP docs do not currently document a dedicated GOAT error
    field. We therefore mark a problem only when the API itself reports a
    non-zero code/failed ret value or when the returned payload contains obvious
    error/fault/alarm/stuck style keys or values.
    """
    code = response.get("code", response.get("status", 0))
    try:
        if int(code) != 0:
            msg = str(response.get("msg") or response.get("message") or f"API-Code {code}")
            return ErrorInfo(True, msg, {"source": "api_code", "code": code, "message": msg})
    except (TypeError, ValueError):
        pass

    state = work_state if isinstance(work_state, dict) else extract_work_state(response)
    ret = str(state.get("ret", response.get("ret", ""))).strip().lower()
    if ret and ret not in _OK_STRINGS:
        return ErrorInfo(True, f"Rückgabewert: {ret}", {"source": "ret", "ret": ret})

    clean = str(state.get("cleanSt", "")).strip().lower()
    charge = str(state.get("chargeSt", "")).strip().lower()
    station = str(state.get("stationSt", "")).strip().lower()
    for field, value in (("cleanSt", clean), ("chargeSt", charge), ("stationSt", station)):
        if _looks_like_error_value(value):
            return ErrorInfo(True, f"{field}: {value}", {"source": field, "value": value})

    found = _find_error_marker(response)
    if found is not None:
        path, key, value = found
        return ErrorInfo(
            True,
            f"{key}: {_short_value(value)}",
            {"source": "payload_marker", "path": path, "key": key, "value": _short_value(value)},
        )

    return ErrorInfo(False, "OK", {})


def _find_error_marker(value: Any, path: str = "$", depth: int = 0) -> tuple[str, str, Any] | None:
    """Find an obvious error-like field in a nested payload."""
    if depth > 8:
        return None
    value = _parse_json_if_string(value)

    if isinstance(value, dict):
        for key, nested in value.items():
            lowered_key = str(key).lower()
            if any(word in lowered_key for word in _ERROR_KEY_WORDS) and _is_errorish_value(nested):
                return (path, str(key), nested)
        for key, nested in value.items():
            found = _find_error_marker(nested, f"{path}.{key}", depth + 1)
            if found is not None:
                return found

    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_error_marker(item, f"{path}[{index}]", depth + 1)
            if found is not None:
                return found

    return None


def _is_errorish_value(value: Any) -> bool:
    """Return true if a field value represents a likely active problem."""
    value = _parse_json_if_string(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _OK_STRINGS:
            return False
        return bool(lowered) and (any(word in lowered for word in _ERROR_VALUE_WORDS) or lowered not in _OK_STRINGS)
    if isinstance(value, dict):
        # A non-empty nested error object usually means an active/known problem,
        # unless it only contains obviously false/zero/OK values.
        return any(_is_errorish_value(nested) for nested in value.values())
    if isinstance(value, list):
        return any(_is_errorish_value(item) for item in value)
    return False


def _looks_like_error_value(value: str) -> bool:
    """Return true if a state code/string is obviously an error."""
    lowered = value.strip().lower()
    if lowered in _OK_STRINGS:
        return False
    return any(word in lowered for word in _ERROR_VALUE_WORDS)


def _short_value(value: Any, limit: int = 160) -> str:
    """Return a short, JSON-ish representation for a sensor state."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"
