"""Regression tests for GOAT Open API reliability helpers."""
from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock

import pytest

from custom_components.ecovacs_goat_openapi.api import (
    EcovacsGoatApiClient,
    EcovacsGoatApiError,
    extract_work_state,
)


class StubClient(EcovacsGoatApiClient):
    """API client with deterministic request responses."""

    def __init__(self, responses):
        self._responses = deque(responses)
        self._preferred_work_state_variant = None
        self.calls = []

    async def _request(self, endpoint, payload, method, **kwargs):
        self.calls.append((method, payload))
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
async def test_return_to_base_uses_documented_command():
    client = StubClient([{"code": 0, "msg": "OK"}])
    await client.async_return_to_base("GOAT")
    assert client.calls == [("post", {"nickName": "GOAT", "cmd": "Charge", "act": "go-start"})]


@pytest.mark.asyncio
async def test_return_to_base_rejection_propagates():
    client = StubClient([EcovacsGoatApiError("rejected")])
    with pytest.raises(EcovacsGoatApiError, match="rejected"):
        await client.async_return_to_base("GOAT")
