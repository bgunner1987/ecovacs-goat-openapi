"""Regression tests for retaining the last valid coordinator state."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.ecovacs_goat_openapi.api import EcovacsGoatAuthError, EcovacsGoatTransientError
from custom_components.ecovacs_goat_openapi.coordinator import EcovacsGoatCoordinator


def coordinator_with(api, previous):
    coordinator = object.__new__(EcovacsGoatCoordinator)
    coordinator.api = api
    coordinator.nickname = "GOAT"
    coordinator.data = previous
    coordinator._local_activity_override = None
    coordinator._local_activity_override_until = None
    return coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"code": -1, "message": "gateway timeout", "data": None},
        {"code": 0, "data": {}},
        {"code": 0, "data": "unexpected"},
    ],
)
async def test_invalid_response_retains_previous_state(response):
    previous = {"work_state": {"cleanSt": "h", "chargeSt": "sc", "stationSt": "i"}}
    api = SimpleNamespace(async_get_work_state=_async_result(response))
    coordinator = coordinator_with(api, previous)
    assert await coordinator._async_update_data() is previous


@pytest.mark.asyncio
async def test_timeout_retains_previous_state():
    previous = {"work_state": {"cleanSt": "s", "chargeSt": "i", "stationSt": "i"}}
    api = SimpleNamespace(async_get_work_state=_async_error(EcovacsGoatTransientError("timeout")))
    coordinator = coordinator_with(api, previous)
    assert await coordinator._async_update_data() is previous


@pytest.mark.asyncio
async def test_auth_error_does_not_return_cached_state():
    previous = {"work_state": {"cleanSt": "s", "chargeSt": "i", "stationSt": "i"}}
    api = SimpleNamespace(async_get_work_state=_async_error(EcovacsGoatAuthError("invalid API key")))
    coordinator = coordinator_with(api, previous)

    with pytest.raises(ConfigEntryAuthFailed) as error:
        await coordinator._async_update_data()
    assert isinstance(error.value.__cause__, EcovacsGoatAuthError)


def _async_result(value):
    async def call(_nickname):
        return value
    return call


def _async_error(error):
    async def call(_nickname):
        raise error
    return call
