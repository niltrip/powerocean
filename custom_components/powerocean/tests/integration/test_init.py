"""test_init: setup, error handling, re-login and migration of config entries."""

import copy
import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

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
        "custom_components.powerocean.coordinator.parse",
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


async def test_still_rejected_after_relogin_fails_update_without_reauth(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """Login works, but the cloud still rejects the request: retry later."""
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    mock_fetch.side_effect = EcoflowApiError("code 500: server error")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    # One login at setup, one after the rejected request, no endless loop
    assert mock_authorize.await_count == 2
    assert mock_fetch.await_count == 3
    assert _soc_state(hass) == STATE_UNAVAILABLE
    assert not _reauth_flows(hass)


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


def _without_battery(sn: str) -> dict:
    """Return the response as if the battery pack `sn` was offline."""
    response = copy.deepcopy(RESPONSE)
    del response["data"]["quota"]["JTS1_BP_STA_REPORT"][sn]
    return response


def _entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    return {
        entity.unique_id: entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }


async def test_battery_offline_at_startup_is_added_later(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """Datapoints missing at startup get their entities with a later update."""
    battery = "SN_BATTERIEPACK02"
    soc = f"{battery}_JTS1_BP_STA_REPORT_bpSoc"
    mock_fetch.return_value = _without_battery(battery)
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    before = _entity_ids(hass, entry)
    assert soc not in before
    assert not dr.async_get(hass).async_get_device(identifiers={(DOMAIN, battery)})

    # The battery is back
    mock_fetch.return_value = RESPONSE
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    after = _entity_ids(hass, entry)
    added = set(after) - set(before)
    assert soc in added
    assert all(unique_id.startswith(f"{battery}_") for unique_id in added)
    # Existing entities are unchanged
    assert {uid: after[uid] for uid in before} == before
    assert hass.states.get(after[soc]).state == "10"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, battery)})
    assert device
    assert device.via_device_id

    # Offline again: the entities stay, unavailable
    mock_fetch.return_value = _without_battery(battery)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _entity_ids(hass, entry) == after
    assert hass.states.get(after[soc]).state == STATE_UNAVAILABLE


def _with_battery_firmware(sn: str, version: int) -> dict:
    """Return the response with another firmware of the battery pack `sn`."""
    response = copy.deepcopy(RESPONSE)
    reports = response["data"]["quota"]["JTS1_BP_STA_REPORT"]
    pack = json.loads(reports[sn])
    pack["moduleAplSwVer"] = version
    reports[sn] = json.dumps(pack)
    return response


async def test_battery_firmware_update_shows_on_device(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """The device page shows the battery firmware, also after an update."""
    battery = "SN_BATTERIEPACK02"
    entry = _entry()
    await _setup(hass, entry)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, battery)})
    assert device
    assert device.sw_version == "4.1.12.2"
    # A firmware sensor is no longer created
    assert not any("moduleAplSwVer" in uid for uid in _entity_ids(hass, entry))

    # Firmware update to 4.1.14.9 (0x04010E09)
    mock_fetch.return_value = _with_battery_firmware(battery, 0x04010E09)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    device = device_registry.async_get_device(identifiers={(DOMAIN, battery)})
    assert device
    assert device.sw_version == "4.1.14.9"


# -----------------------------
# removing devices
# -----------------------------


async def _remove_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entry: MockConfigEntry,
    sn: str,
) -> bool:
    """Remove the device `sn` as the device page does, return True on success."""
    assert await async_setup_component(hass, "config", {})
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, sn)})
    assert device
    client = await hass_ws_client(hass)
    response = await client.remove_device(device.id, entry.entry_id)
    return response["success"]


@pytest.mark.parametrize("sn", [SN, "SN_BATTERIEPACK02"])
async def test_device_with_values_cannot_be_removed(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_authorize: AsyncMock,
    mock_fetch: AsyncMock,
    sn: str,
) -> None:
    entry = _entry()
    await _setup(hass, entry)

    assert not await _remove_device(hass, hass_ws_client, entry, sn)
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, sn)})


async def test_device_without_values_can_be_removed_and_returns(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_authorize: AsyncMock,
    mock_fetch: AsyncMock,
) -> None:
    """A device missing in the response can be removed, and comes back with it."""
    battery = "SN_BATTERIEPACK02"
    soc = f"{battery}_JTS1_BP_STA_REPORT_bpSoc"
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    before = _entity_ids(hass, entry)

    # The battery is gone (e.g. replaced) and is removed by the user
    mock_fetch.return_value = _without_battery(battery)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert await _remove_device(hass, hass_ws_client, entry, battery)
    await hass.async_block_till_done()

    assert not dr.async_get(hass).async_get_device(identifiers={(DOMAIN, battery)})
    remaining = _entity_ids(hass, entry)
    assert soc not in remaining
    assert all(not unique_id.startswith(f"{battery}_") for unique_id in remaining)
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SN)})

    # It is back: device and entities are added again
    mock_fetch.return_value = RESPONSE
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, battery)})
    assert _entity_ids(hass, entry).keys() == before.keys()
    assert hass.states.get(_entity_ids(hass, entry)[soc]).state == "10"


# -----------------------------
# migration
# -----------------------------


# Config entry as created by releases 2024.01.01 (version 1) up to
# 2025.08.28 (version 1.3)
LEGACY_V1_DATA = {
    "user_input": DATA,
    "device_info": {"name": "PowerOcean", "serial": SN},
    "options": {"friendly_name": "Garage", "scan_interval": 30},
}


@pytest.mark.parametrize("version", [1, 1.3])
async def test_migrate_v1_entry(
    hass: HomeAssistant,
    mock_authorize: AsyncMock,
    mock_fetch: AsyncMock,
    version: float,
) -> None:
    entry = _entry(data=LEGACY_V1_DATA, options={}, version=version, minor_version=1)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (2, 5)
    assert dict(entry.data) == DATA
    assert dict(entry.options) == {"friendly_name": "Garage", "scan_interval": 30}
    assert entry.runtime_data.update_interval.total_seconds() == 30
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SN)})
    assert device.name == "Garage"


async def test_migrate_v2_1_restores_name_lost_by_earlier_migration(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """Releases 2026.02.x set the default name when migrating from 1.x."""
    entry = _entry(
        data={**DATA, "device_info": {}, "options": LEGACY_V1_DATA["options"]},
        options={"friendly_name": "PowerOcean", "scan_interval": 30},
        minor_version=1,
    )
    await _setup(hass, entry)

    assert dict(entry.options) == {"friendly_name": "Garage", "scan_interval": 30}
    assert dict(entry.data) == DATA


async def test_migrate_v2_1_keeps_name_set_later(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        data={**DATA, "options": LEGACY_V1_DATA["options"]},
        options={"friendly_name": "Keller", "scan_interval": 20},
        minor_version=1,
    )
    await _setup(hass, entry)

    assert dict(entry.options) == {"friendly_name": "Keller", "scan_interval": 20}


async def test_migrate_v2_1_backfills_missing_options(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        data={**DATA, "options": {"scan_interval": 30, "friendly_name": "Garage"}},
        options={},
        minor_version=1,
    )
    await _setup(hass, entry)

    assert (entry.version, entry.minor_version) == (2, 5)
    assert dict(entry.options) == {"friendly_name": "Garage", "scan_interval": 30}
    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_migrate_v2_1_keeps_existing_options(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(
        options={"friendly_name": "Keller", "scan_interval": 20}, minor_version=1
    )
    await _setup(hass, entry)

    assert (entry.version, entry.minor_version) == (2, 5)
    assert dict(entry.options) == {"friendly_name": "Keller", "scan_interval": 20}
    assert dict(entry.data) == DATA


async def test_migrate_v2_2_removes_battery_firmware_sensor(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """The battery firmware moved from a sensor to the device page."""
    entry = _entry(minor_version=2)
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    old = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "SN_BATTERIEPACK01_JTS1_BP_STA_REPORT_moduleAplSwVer",
        config_entry=entry,
    )
    kept = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "SN_BATTERIEPACK01_JTS1_BP_STA_REPORT_bpSoc",
        config_entry=entry,
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 5)
    assert entity_registry.async_get(old.entity_id) is None
    assert entity_registry.async_get(kept.entity_id)


async def test_migrate_v2_3_moves_system_values_off_the_wallbox(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """System values of EDEV_SYS_REPORT moved from the wallbox to the inverter."""
    entry = _entry(minor_version=3)
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    old = entity_registry.async_get_or_create(
        "sensor", DOMAIN, "SN_DEV01_JT303_EDEV_SYS_REPORT_socCur", config_entry=entry
    )
    device_value = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "SN_DEV01_JT303_EDEV_SYS_REPORT_allocatedPower",
        config_entry=entry,
    )
    inverter_value = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{SN}_JT303_EDEV_SYS_REPORT_socCur", config_entry=entry
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 5)
    assert entity_registry.async_get(old.entity_id) is None
    assert entity_registry.async_get(device_value.entity_id)
    assert entity_registry.async_get(inverter_value.entity_id)


async def test_migrate_v2_4_removes_serial_number_sensors(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    """The serial number is in the device info, no longer a sensor."""
    entry = _entry(minor_version=4)
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    old = [
        entity_registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=entry
        )
        for unique_id in (
            "SN_BATTERIEPACK01_JTS1_BP_STA_REPORT_bpSn",
            "SN_DEV01_JTS1_EDEV_PARAM_REPORT_devSn",
            "SN_HR01_JTS1_HEATING_ROD_PARAM_REPORT_hrSn",
            "SN_EV01_RE307_EVCHARGING_REPORT_evSn",
        )
    ]
    kept = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "SN_BATTERIEPACK01_JTS1_BP_STA_REPORT_bpSoc",
        config_entry=entry,
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 5)
    assert all(entity_registry.async_get(e.entity_id) is None for e in old)
    assert entity_registry.async_get(kept.entity_id)


async def test_migrate_from_future_version_fails(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_fetch: AsyncMock
) -> None:
    entry = _entry(version=3, minor_version=1)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
