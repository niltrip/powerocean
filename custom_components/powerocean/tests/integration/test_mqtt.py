"""test_mqtt: optional MQTT connection for the firmware of the modules."""

import json
from collections.abc import Callable, Generator
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.powerocean.api import EcoflowConnectionError
from custom_components.powerocean.const import DOMAIN
from custom_components.powerocean.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.powerocean.ecoflow import MOCK_RESPONSE_ENV
from custom_components.powerocean.mqtt import ModuleVersion, module_versions

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RESPONSE = json.loads((FIXTURES_DIR / "response_modified.json").read_text("utf-8"))
# get_reply with the module info, see unit/test_mqtt.py
MODULES = module_versions(
    bytes.fromhex((FIXTURES_DIR / "mqtt_module_info.hex").read_text())
)

SN = "SN_INVERTERBOX01"
METER = f"{SN}_meter106"
USER_ID = "1234567890"
CREDENTIALS = {
    "url": "mqtt.example.com",
    "port": "8883",
    "certificateAccount": "app-account",
    "certificatePassword": "app-password",
}


class FakeMqtt:
    """Stands in for PowerOceanMqtt: no network, records the calls."""

    instances: ClassVar[list["FakeMqtt"]] = []

    def __init__(
        self,
        credentials: dict[str, Any],
        serial: str,
        user_id: str,
        on_modules: Callable[[dict[int, ModuleVersion]], None],
    ) -> None:
        """Record the arguments, as PowerOceanMqtt gets them."""
        self.credentials = credentials
        self.serial = serial
        self.user_id = user_id
        self.on_modules = on_modules
        self.started = self.stopped = False
        self.requests = 0
        FakeMqtt.instances.append(self)

    def start(self) -> None:
        """Connect, without network."""
        self.started = True

    def stop(self) -> None:
        """Disconnect."""
        self.stopped = True

    def request_full_state(self) -> None:
        """Count the requests."""
        self.requests += 1


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def cloud() -> Generator[AsyncMock]:
    """Login with user ID, HTTP response and MQTT credentials; fake client."""

    async def login(api: Any) -> None:
        api.user_id = USER_ID

    FakeMqtt.instances.clear()
    with (
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.async_authorize",
            autospec=True,
            side_effect=login,
        ),
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.fetch_raw",
            return_value=RESPONSE,
        ),
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.async_mqtt_credentials",
            return_value=CREDENTIALS,
        ) as credentials,
        patch("custom_components.powerocean.coordinator.PowerOceanMqtt", FakeMqtt),
    ):
        yield credentials


async def _setup(hass: HomeAssistant, *, mqtt: bool | None) -> MockConfigEntry:
    options: dict[str, Any] = {"friendly_name": "PowerOcean", "scan_interval": 10}
    if mqtt is not None:
        options["mqtt"] = mqtt
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=5,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data={
            "device_id": SN,
            "email": "test@example.com",
            "password": "password123",
            "model_id": "83",
        },
        options=options,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _sw_version(hass: HomeAssistant, sn: str) -> str | None:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, sn)})
    assert device
    return device.sw_version


@pytest.mark.parametrize("mqtt", [None, False])
async def test_off_by_default(
    hass: HomeAssistant,
    cloud: AsyncMock,
    mqtt: bool | None,  # noqa: FBT001 (pytest parameter)
) -> None:
    entry = await _setup(hass, mqtt=mqtt)

    assert entry.state is ConfigEntryState.LOADED
    cloud.assert_not_called()
    assert not FakeMqtt.instances
    assert entry.runtime_data.mqtt is None


async def test_module_firmware_on_the_devices(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances
    assert client.started
    assert (client.credentials, client.serial, client.user_id) == (
        CREDENTIALS,
        SN,
        USER_ID,
    )

    # paho calls back from its own thread
    await hass.async_add_executor_job(client.on_modules, MODULES)
    await hass.async_block_till_done()

    # EMS firmware on the inverter, meter firmware on the smart meter
    assert _sw_version(hass, SN) == "5.1.38.5"
    assert _sw_version(hass, METER) == "1.0.0.0"

    # The next HTTP poll does not overwrite them
    await entry.runtime_data.async_refresh()
    assert _sw_version(hass, SN) == "5.1.38.5"
    assert _sw_version(hass, METER) == "1.0.0.0"

    # All modules in the diagnostics, without serial numbers
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["coordinator"]["mqtt"] is True
    assert diagnostics["modules"][4] == {
        "type": 2,
        "firmware": "5.1.23.17",
        "loader": "2.2.0.1",
    }

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert client.stopped


async def test_switched_off_removes_the_firmware(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances
    await hass.async_add_executor_job(client.on_modules, MODULES)
    await hass.async_block_till_done()
    battery_firmware = dict(entry.runtime_data.firmware)
    del battery_firmware[SN], battery_firmware[METER]

    # As the options flow does: save, then reload
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "mqtt": False}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert client.stopped
    assert len(FakeMqtt.instances) == 1
    assert _sw_version(hass, SN) is None
    assert _sw_version(hass, METER) is None
    # The battery firmware comes from HTTP and stays
    assert battery_firmware
    for sn, version in battery_firmware.items():
        assert _sw_version(hass, sn) == version


async def test_full_state_requested_again(
    hass: HomeAssistant, cloud: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    freezer.tick(timedelta(hours=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert client.requests == 1


async def test_late_message_after_unload_is_ignored(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    coordinator = entry.runtime_data
    (client,) = FakeMqtt.instances
    assert await hass.config_entries.async_unload(entry.entry_id)

    await hass.async_add_executor_job(client.on_modules, MODULES)
    await hass.async_block_till_done()

    assert coordinator.modules == {}


@pytest.mark.parametrize(
    "error", [EcoflowConnectionError("offline"), OSError("certificates")]
)
async def test_start_failure_keeps_the_integration_running(
    hass: HomeAssistant,
    cloud: AsyncMock,
    error: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(FakeMqtt, "start", side_effect=error):
        if isinstance(error, EcoflowConnectionError):
            cloud.side_effect = error
        entry = await _setup(hass, mqtt=True)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.mqtt is None
    assert "MQTT not started" in caplog.text


async def test_no_mqtt_without_user_id(hass: HomeAssistant, cloud: AsyncMock) -> None:
    with patch(
        "custom_components.powerocean.ecoflow.HAEcoflowApi.async_authorize",
        return_value=None,
    ):
        entry = await _setup(hass, mqtt=True)

    assert entry.state is ConfigEntryState.LOADED
    cloud.assert_not_called()
    assert not FakeMqtt.instances


async def test_no_mqtt_with_mock_response(
    hass: HomeAssistant, cloud: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MOCK_RESPONSE_ENV, "response_modified.json")

    entry = await _setup(hass, mqtt=True)

    assert entry.state is ConfigEntryState.LOADED
    cloud.assert_not_called()
    assert not FakeMqtt.instances
