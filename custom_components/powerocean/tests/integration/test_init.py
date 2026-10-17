"""test_init: setup, error handling, re-login and migration of config entries."""

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.api import (
    AuthenticationError,
    EcoflowApiError,
    EcoflowConnectionError,
)
from custom_components.powerocean.const import DOMAIN
from custom_components.powerocean.coordinator import PowerOceanCoordinator

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RESPONSE = json.loads((FIXTURES_DIR / "response_modified.json").read_text("utf-8"))

SN = "SN_INVERTERBOX01"
DATA = {
    "device_id": SN,
    "email": "test@example.com",
    "password": "password123",
    "model_id": "83",
}
OPTIONS = {"friendly_name": "PowerOcean", "scan_interval": 10}
# A sensor that exists in response_modified.json
SOC_UNIQUE_ID = f"{SN}_JTS1_EMS_CHANGE_REPORT_bpSoc"


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def mock_authorize() -> Generator[AsyncMock]:
    with patch(
        "custom_components.powerocean.ecoflow.HAEcoflowApi.async_authorize",
        return_value=None,
    ) as mock:
        yield mock


@pytest.fixture
def mock_fetch() -> Generator[AsyncMock]:
    with patch(
        "custom_components.powerocean.ecoflow.HAEcoflowApi.fetch_raw",
        return_value=RESPONSE,
    ) as mock:
        yield mock


def _entry(
    data: dict | None = None,
    options: dict | None = None,
    version: int = 2,
    minor_version: int = 2,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=version,
        minor_version=minor_version,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data=DATA if data is None else data,
        options=OPTIONS if options is None else options,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _soc_state(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, SOC_UNIQUE_ID)
    assert entity_id
    return hass.states.get(entity_id).state


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


# -----------------------------
# setup / unload
# -----------------------------


async def test_setup_and_unload(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, PowerOceanCoordinator)
    assert entry.runtime_data.endpoints
    # Structure and values come from the same (single) request
    mock_authorize.assert_awaited_once()
    mock_fetch.assert_awaited_once()
    assert _soc_state(hass) not in (STATE_UNAVAILABLE, "unknown")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    "side_effect", [EcoflowConnectionError("offline"), EcoflowApiError("region")]
)
async def test_setup_retries_when_cloud_unreachable(
    hass: HomeAssistant,
    mock_authorize: AsyncMock,
    mock_fetch: AsyncMock,
    side_effect: Exception,
) -> None:
    """Regression: no internet at HA start used to fail setup permanently."""
    mock_authorize.side_effect = side_effect
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not _reauth_flows(hass)


async def test_setup_starts_reauth_on_invalid_credentials(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    mock_authorize.side_effect = AuthenticationError("wrong password")
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_setup_retries_when_first_fetch_fails(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    mock_fetch.side_effect = EcoflowConnectionError("timeout")
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_on_unparsable_response(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry()
    with patch(
        "custom_components.powerocean.coordinator.EcoflowParser.parse_values",
        side_effect=KeyError("mpptPv"),
    ):
        await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_fails_without_device_id(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(data={**DATA, "device_id": ""})
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    mock_authorize.assert_not_awaited()


async def test_main_device_uses_friendly_name(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    entry = _entry(options={**OPTIONS, "friendly_name": "Keller"})
    await _setup(hass, entry)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SN)})
    assert device
    assert device.name == "Keller"
    assert device.model == "PowerOcean"


# -----------------------------
# coordinator updates and re-login
# -----------------------------


async def test_relogin_when_token_rejected(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """An expired token must not leave the integration unavailable."""
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    mock_fetch.side_effect = [EcoflowApiError("code 8521: token expired"), RESPONSE]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert mock_authorize.await_count == 2
    assert _soc_state(hass) not in (STATE_UNAVAILABLE, "unknown")


async def test_relogin_with_invalid_credentials_starts_reauth(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    mock_fetch.side_effect = AuthenticationError("token rejected")
    mock_authorize.side_effect = AuthenticationError("password changed")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert len(_reauth_flows(hass)) == 1
    assert _soc_state(hass) == STATE_UNAVAILABLE


async def test_connection_error_does_not_relogin_and_recovers(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    mock_fetch.side_effect = EcoflowConnectionError("timeout")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert mock_authorize.await_count == 1
    assert _soc_state(hass) == STATE_UNAVAILABLE
    assert not _reauth_flows(hass)

    mock_fetch.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert _soc_state(hass) not in (STATE_UNAVAILABLE, "unknown")


async def test_scan_interval_from_options(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(options={**OPTIONS, "scan_interval": 42})
    await _setup(hass, entry)

    assert entry.runtime_data.update_interval.total_seconds() == 42


# -----------------------------
# migration
# -----------------------------


async def test_migrate_v1_entry(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        data={"user_input": {**DATA, "friendly_name": "Alt"}},
        options={},
        version=1,
        minor_version=1,
    )
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.data) == DATA
    assert dict(entry.options) == {"friendly_name": "Alt", "scan_interval": 10}


async def test_migrate_v2_1_backfills_missing_options(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        data={**DATA, "options": {"scan_interval": 30, "friendly_name": "Garage"}},
        options={},
        minor_version=1,
    )
    await _setup(hass, entry)

    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.options) == {"friendly_name": "Garage", "scan_interval": 30}
    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_migrate_v2_1_keeps_existing_options(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        options={"friendly_name": "Keller", "scan_interval": 20}, minor_version=1
    )
    await _setup(hass, entry)

    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.options) == {"friendly_name": "Keller", "scan_interval": 20}
    assert dict(entry.data) == DATA


async def test_migrate_from_future_version_fails(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(version=3, minor_version=1)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
