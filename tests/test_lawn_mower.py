"""Regression tests for Home Assistant lawn mower command handling."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from homeassistant.components.lawn_mower import LawnMowerEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
import pytest

from custom_components.ecovacs_goat_openapi.api import EcovacsGoatApiError

# Home Assistant 2025.1 (the newest release installable on the bundled Python
# 3.12 runtime) predates this typing-only alias used by newer HA releases.
if not hasattr(entity_platform, "AddConfigEntryEntitiesCallback"):
    entity_platform.AddConfigEntryEntitiesCallback = Any

from custom_components.ecovacs_goat_openapi.lawn_mower import EcovacsGoatLawnMower


def test_pause_feature_is_advertised():
    entity = object.__new__(EcovacsGoatLawnMower)
    assert entity.supported_features & LawnMowerEntityFeature.PAUSE


@pytest.mark.asyncio
async def test_pause_refreshes_without_faking_paused_state():
    coordinator = SimpleNamespace(
        api=SimpleNamespace(async_pause_mowing=AsyncMock(return_value={"code": 0})),
        async_set_local_activity=Mock(),
        async_request_refresh=AsyncMock(),
    )
    entity = SimpleNamespace(coordinator=coordinator, _nickname="GOAT")

    await EcovacsGoatLawnMower.async_pause(entity)

    coordinator.api.async_pause_mowing.assert_awaited_once_with("GOAT")
    coordinator.async_set_local_activity.assert_called_once_with(None)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_pause_rejection_becomes_home_assistant_error():
    coordinator = SimpleNamespace(
        api=SimpleNamespace(async_pause_mowing=AsyncMock(side_effect=EcovacsGoatApiError("rejected"))),
        async_set_local_activity=Mock(),
        async_request_refresh=AsyncMock(),
    )
    entity = SimpleNamespace(coordinator=coordinator, _nickname="GOAT")

    with pytest.raises(HomeAssistantError, match="konnte nicht pausiert werden"):
        await EcovacsGoatLawnMower.async_pause(entity)

    coordinator.async_set_local_activity.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()
