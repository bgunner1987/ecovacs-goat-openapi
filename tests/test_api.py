"""Regression tests for GOAT Open API reliability helpers."""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock

from aiohttp import ClientResponseError
import pytest

from custom_components.ecovacs_goat_openapi.api import (
    EcovacsGoatApiClient,
    EcovacsGoatApiError,
    EcovacsGoatAuthError,
    EcovacsGoatTransientError,
    extract_work_state,
    is_transient_api_error,
)
from custom_components.ecovacs_goat_openapi.const import ENDPOINT_ROBOT_CTL


class StubClient(EcovacsGoatApiClient):
    """API client with deterministic request responses."""

    def __init__(self, responses):
        self._responses = deque(responses)
        self._preferred_work_state_variant = None
        self.calls = []

    async def _request(self, endpoint, payload, method, **kwargs):
        self.calls.append((method, payload, kwargs))
        result = self._responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"code": 0, "data": {"ctl": {"data": {"cleanSt": "s", "chargeSt": "i", "stationSt": "i"}}}},
         {"cleanSt": "s", "chargeSt": "i", "stationSt": "i"}),
        ({"data": '{"result":{"cleanSt":"h","chargeSt":"sc","stationSt":"i"}}'},
         {"cleanSt": "h", "chargeSt": "sc", "stationSt": "i"}),
    ],
)
def test_extract_work_state_nested_shapes(response, expected):
    assert extract_work_state(response) == expected


@pytest.mark.asyncio
async def test_fallback_becomes_preferred_and_is_used_first():
    valid = {"code": 0, "data": {"cleanSt": "h", "chargeSt": "i", "stationSt": "i"}}
    client = StubClient([{"code": -1}, valid, valid])

    first = await client.async_get_work_state("GOAT")
    assert first["_ha_work_state_query"] == "post_nickname"
    assert len(client.calls) == 2

    await client.async_get_work_state("GOAT")
    assert len(client.calls) == 3
    assert "nickname" in client.calls[-1][1]


@pytest.mark.asyncio
async def test_preferred_variant_succeeds_without_fallback_and_is_reused():
    valid = {"code": 0, "data": {"cleanSt": "h", "chargeSt": "i", "stationSt": "i"}}
    client = StubClient([valid, valid])

    await client.async_get_work_state("GOAT")
    await client.async_get_work_state("GOAT")

    assert len(client.calls) == 2
    assert all("nickName" in call[1] for call in client.calls)


@pytest.mark.parametrize(
    "response",
    [
        {"code": -1, "message": "Request failed with status code 502"},
        {"code": -1, "message": "Request failed with status code 503"},
        {"code": -1, "message": "Request failed with status code 504"},
        {"code": -1, "message": "timeout"},
        {"code": -1, "message": "low level socket timed out"},
    ],
)
def test_api_body_error_is_transient(response):
    assert is_transient_api_error(response)


def test_auth_body_error_is_not_transient():
    assert not is_transient_api_error({"code": -1, "message": "permission denied after socket timeout"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"code": -1, "message": "Request failed with status code 504"},
        {"code": -1, "message": "socket timeout"},
    ],
)
async def test_transient_body_retries_preferred_variant_before_fallback(monkeypatch, response):
    valid = {"code": 0, "data": {"cleanSt": "h", "chargeSt": "i", "stationSt": "i"}}
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.ecovacs_goat_openapi.api.asyncio.sleep", sleep)
    client = StubClient([response, response, valid])
    client._preferred_work_state_variant = "post_nickname"

    result = await client.async_get_work_state("GOAT")

    assert result["_ha_work_state_query"] == "post_nickName"
    assert len(client.calls) == 3
    assert client.calls[0][1] == client.calls[1][1]
    assert client.calls[2][1] != client.calls[1][1]
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_transient_retries_are_bounded(monkeypatch):
    response = {"code": -1, "message": "Request failed with status code 503"}
    monkeypatch.setattr("custom_components.ecovacs_goat_openapi.api.asyncio.sleep", AsyncMock())
    client = StubClient([response] * 5)

    result = await client.async_get_work_state("GOAT")

    assert result["_ha_work_state_query"] == "no_variant_returned_work_state"
    assert len(client.calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_http_status_retry_is_bounded(monkeypatch, status):
    session = FakeSession([status, status])
    client = EcovacsGoatApiClient(session, "api-key", "https://open.ecovacs.com")
    monkeypatch.setattr("custom_components.ecovacs_goat_openapi.api.asyncio.sleep", AsyncMock())

    with pytest.raises(EcovacsGoatTransientError):
        await client._request(
            ENDPOINT_ROBOT_CTL,
            {"nickName": "GOAT"},
            method="post",
            retry_transient=True,
        )

    assert session.post_calls == 2


@pytest.mark.asyncio
async def test_api_body_auth_error_propagates():
    client = StubClient([{"code": -1, "message": "invalid API key"}])
    with pytest.raises(EcovacsGoatAuthError):
        await client.async_get_work_state("GOAT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (lambda client: client.async_start_mowing("GOAT"), {"nickName": "GOAT", "cmd": "Clean", "act": "s"}),
        (
            lambda client: client.async_start_mowing("GOAT", resume=True),
            {"nickName": "GOAT", "cmd": "Clean", "act": "r"},
        ),
        (lambda client: client.async_pause_mowing("GOAT"), {"nickName": "GOAT", "cmd": "Clean", "act": "p"}),
        (
            lambda client: client.async_return_to_base("GOAT"),
            {"nickName": "GOAT", "cmd": "Charge", "act": "go-start"},
        ),
    ],
)
async def test_command_payloads(operation, expected):
    client = StubClient([{"code": 0, "msg": "OK"}])
    await operation(client)
    assert client.calls[0][:2] == ("post", expected)


@pytest.mark.asyncio
async def test_return_to_base_rejection_propagates():
    client = StubClient([EcovacsGoatApiError("rejected")])
    with pytest.raises(EcovacsGoatApiError, match="rejected"):
        await client.async_return_to_base("GOAT")


class FakeResponse:
    """Minimal aiohttp response used to exercise HTTP retry handling."""

    def __init__(self, status):
        self.status = status
        self.url = "https://open.ecovacs.com/robot/ctl"

    def raise_for_status(self):
        raise ClientResponseError(None, (), status=self.status)


class FakeResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, statuses):
        self.statuses = deque(statuses)
        self.post_calls = 0

    def post(self, *args, **kwargs):
        self.post_calls += 1
        return FakeResponseContext(FakeResponse(self.statuses.popleft()))
