"""test_mock_response: a saved response replaces the EcoFlow cloud (development)."""

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.const import DOMAIN
from custom_components.powerocean.ecoflow import MOCK_RESPONSE_ENV, mock_response_path

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
DUAL = "response_modified_po_dual.json"
SN = "SN_INVERTERBOX01"


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def no_cloud() -> Generator[tuple[AsyncMock, AsyncMock]]:
    """Fail if the EcoFlow cloud is contacted."""
    with (
        patch(
            "custom_components.powerocean.api.EcoflowApi.async_authorize",
            side_effect=AssertionError("login"),
        ) as authorize,
        patch(
            "custom_components.powerocean.api.EcoflowApi.fetch_raw",
            side_effect=AssertionError("request"),
        ) as fetch,
    ):
        yield authorize, fetch
    authorize.assert_not_called()
    fetch.assert_not_called()


def _entry(sn: str = SN, variant: str = "83") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=f"PowerOcean {sn}",
        title="PowerOcean",
        data={
            "device_id": sn,
            "email": "test@example.com",
            "password": "egal",
            "model_id": variant,
        },
        options={"friendly_name": "PowerOcean", "scan_interval": 10},
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def test_no_mock_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MOCK_RESPONSE_ENV, raising=False)
    assert mock_response_path() is None


async def test_dual_system_without_cloud(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, no_cloud: tuple
) -> None:
    """The dual fixture shows the same entities as its golden master."""
    monkeypatch.setenv(MOCK_RESPONSE_ENV, DUAL)
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    master = json.loads(
        (FIXTURES_DIR / f"golden_master_entities_{DUAL}").read_text("utf-8")
    )
    assert {e.unique_id for e in entities} == set(master["entities"])
    assert dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "SN_INVERTERBOX02")}
    )


async def test_mock_from_diagnostics_download(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_cloud: tuple,
) -> None:
    response = json.loads((FIXTURES_DIR / "response_modified.json").read_text())
    download = tmp_path / "download.json"
    download.write_text(json.dumps({"data": {"api_response": response}}))
    monkeypatch.setenv(MOCK_RESPONSE_ENV, str(download))
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_missing_mock_response_retries_setup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, no_cloud: tuple
) -> None:
    monkeypatch.setenv(MOCK_RESPONSE_ENV, "gibt_es_nicht.json")
    entry = _entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_config_flow_without_cloud(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, no_cloud: tuple
) -> None:
    """Any serial number and password can be set up with a mocked response."""
    monkeypatch.setenv(MOCK_RESPONSE_ENV, DUAL)
    with patch("custom_components.powerocean.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "device_id": SN,
                "email": "test@example.com",
                "password": "egal",
                "model_id": "83",
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"friendly_name": "Dual", "scan_interval": 10}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
