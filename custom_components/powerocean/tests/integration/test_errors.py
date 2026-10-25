"""test_errors: error count sensor with the active error codes."""

import copy
import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.const import DOMAIN

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RESPONSE = json.loads(
    (FIXTURES_DIR / "response_modified_error_521.json").read_text("utf-8")
)

SN = "SN_INVERTERBOX01"
ERROR_COUNT_UNIQUE_ID = f"{SN}_JTS1_ERROR_CHANGE_REPORT_errorCount"


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def mock_fetch() -> Generator[AsyncMock]:
    with (
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.async_authorize",
            return_value=None,
        ),
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.fetch_raw",
            return_value=RESPONSE,
        ) as mock,
    ):
        yield mock


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data={
            "device_id": SN,
            "email": "test@example.com",
            "password": "password123",
            "model_id": "83",
        },
        options={"friendly_name": "PowerOcean", "scan_interval": 10},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _error_sensor(hass: HomeAssistant) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, ERROR_COUNT_UNIQUE_ID
    )
    assert entity_id
    return hass.states.get(entity_id)


async def test_active_error(hass: HomeAssistant, mock_fetch: AsyncMock) -> None:
    await _setup(hass)

    state = _error_sensor(hass)
    assert state.state == "1"
    assert state.attributes["errors"] == [
        {
            "module": "ems",
            "module_sn": SN,
            "code": 521,
            "name": "Energy storage communication error",
            "level": "Warning",
        }
    ]
    assert "user-portal.ecoflow.com" in state.attributes["error_details"]


async def test_error_cleared(hass: HomeAssistant, mock_fetch: AsyncMock) -> None:
    entry = await _setup(hass)

    response = copy.deepcopy(RESPONSE)
    response["data"]["quota"]["JTS1_ERROR_CHANGE_REPORT"]["emsErrCode"]["errCode"] = []
    mock_fetch.return_value = response
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = _error_sensor(hass)
    assert state.state == "0"
    assert state.attributes["errors"] == []
    assert "error_details" not in state.attributes
