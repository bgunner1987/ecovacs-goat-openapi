"""Regression tests for retaining the last valid coordinator state."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.ecovacs_goat_openapi.api import EcovacsGoatAuthError, EcovacsGoatTransientError
from custom_components.ecovacs_goat_openapi.coordinator import EcovacsGoatCoordinator

LOGGER = "custom_components.ecovacs_goat_openapi.coordinator"

OFFLINE_STATE = {
    "ret": "fail",
    "errno": 4200,
    "msg": '{"ret":"fail","errno":4200,"error":"endpoint offline"}',
}
TIMEOUT_STATE = {
    "ret": "fail",
    "errno": 10004,
    "msg": '{"errno":500,"error":"http request low level socket timed out"}',
}
VALID_STATE = {"cleanSt": "h", "chargeSt": "sc", "stationSt": "i"}


def coordinator_with(api, previous):
    coordinator = object.__new__(EcovacsGoatCoordinator)
    coordinator.api = api
    coordinator.nickname = "GOAT"
    coordinator.data = previous
    coordinator._local_activity_override = None
    coordinator._local_activity_override_until = None
    coordinator._last_work_state_error = None
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("work_state", "expected_message"),
    [
        (OFFLINE_STATE, "is offline (errno=4200)"),
        (TIMEOUT_STATE, "communication timeout (errno=10004)"),
        (
            {**OFFLINE_STATE, "ret": " FAIL ", "errno": "4200"},
            "is offline (errno=4200)",
        ),
    ],
)
async def test_known_temporary_failure_warns_once_without_schema_warning(
    caplog, work_state, expected_message
):
    previous = {"work_state": VALID_STATE}
    response = _work_state_response(work_state)
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_results(response, response)),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    assert await coordinator._async_update_data() is previous
    assert await coordinator._async_update_data() is previous

    warnings = _coordinator_warnings(caplog)
    assert sum(expected_message in record.message for record in warnings) == 1
    assert not any("did not contain cleanSt/chargeSt/stationSt" in record.message for record in warnings)
    assert not any("attempts=" in record.message or "raw_response=" in record.message for record in warnings)


@pytest.mark.asyncio
async def test_temporary_failure_change_warns_once_per_state(caplog):
    previous = {"work_state": VALID_STATE}
    responses = [
        _work_state_response(OFFLINE_STATE),
        _work_state_response(OFFLINE_STATE),
        _work_state_response(TIMEOUT_STATE),
        _work_state_response(TIMEOUT_STATE),
    ]
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_results(*responses)),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    for _ in responses:
        assert await coordinator._async_update_data() is previous

    warnings = _coordinator_warnings(caplog)
    assert sum("errno=4200" in record.message for record in warnings) == 1
    assert sum("errno=10004" in record.message for record in warnings) == 1


@pytest.mark.asyncio
async def test_recovery_is_logged_once_and_resets_error_state(caplog):
    previous = {"work_state": VALID_STATE}
    responses = [
        _work_state_response(OFFLINE_STATE),
        _work_state_response(VALID_STATE),
        _work_state_response(VALID_STATE),
    ]
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_results(*responses)),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    recovery_records = [
        record for record in caplog.records if record.name == LOGGER and "is reachable again" in record.message
    ]
    assert len(recovery_records) == 1
    assert coordinator._last_work_state_error is None


@pytest.mark.asyncio
async def test_offline_after_recovery_warns_again(caplog):
    previous = {"work_state": VALID_STATE}
    responses = [
        _work_state_response(OFFLINE_STATE),
        _work_state_response(VALID_STATE),
        _work_state_response(OFFLINE_STATE),
    ]
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_results(*responses)),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    for _ in responses:
        await coordinator._async_update_data()

    assert sum("is offline (errno=4200)" in record.message for record in _coordinator_warnings(caplog)) == 2


@pytest.mark.asyncio
async def test_unknown_failure_remains_visible(caplog):
    previous = {"work_state": VALID_STATE}
    unknown = {"ret": "fail", "errno": 9999, "msg": "unknown error"}
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_result(_work_state_response(unknown))),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    assert await coordinator._async_update_data() is previous

    warnings = _coordinator_warnings(caplog)
    assert any("did not contain cleanSt/chargeSt/stationSt" in record.message for record in warnings)
    assert any("errno=9999" in record.message for record in warnings)


@pytest.mark.asyncio
async def test_unexpected_schema_without_status_fields_still_warns(caplog):
    previous = {"work_state": VALID_STATE}
    response = {"code": 0, "msg": "OK", "data": {"unexpected": True}}
    coordinator = coordinator_with(
        SimpleNamespace(async_get_work_state=_async_result(response)),
        previous,
    )
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    assert await coordinator._async_update_data() is previous

    assert any(
        "did not contain cleanSt/chargeSt/stationSt" in record.message
        for record in _coordinator_warnings(caplog)
    )


def _async_result(value):
    async def call(_nickname):
        return value
    return call


def _async_results(*values):
    responses = iter(values)

    async def call(_nickname):
        return next(responses)

    return call


def _async_error(error):
    async def call(_nickname):
        raise error
    return call


def _work_state_response(work_state):
    return {
        "code": 0,
        "msg": "OK",
        "data": work_state,
        "_ha_work_state_query": "post_nickName",
        "_ha_work_state_attempts": [{"variant": "post_nickName", "code": 0}],
    }


def _coordinator_warnings(caplog):
    return [
        record
        for record in caplog.records
        if record.name == LOGGER and record.levelno >= logging.WARNING
    ]
