"""test_mqtt: optional MQTT connection for the firmware of the modules."""

import copy
import json
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
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
from custom_components.powerocean.mqtt import MqttCallbacks, module_versions
from custom_components.powerocean.tests.utils import get_device

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

    # Acknowledge commands like the system (False: no answer)
    acknowledge = True

    def __init__(
        self,
        credentials: dict[str, Any],
        serial: str,
        user_id: str,
        callbacks: MqttCallbacks,
    ) -> None:
        """Record the arguments, as PowerOceanMqtt gets them."""
        self.credentials = credentials
        self.serial = serial
        self.user_id = user_id
        self.callbacks = callbacks
        self.on_modules = callbacks.on_modules
        self.started = self.stopped = False
        self.requests = 0
        self.led_commands: list[int] = []
        self.soc_limit_commands: list[tuple[int, int, int]] = []
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

    def set_led_brightness(self, percent: int, seq: int) -> None:
        """Record the command; the system acknowledges it (paho's thread)."""
        self.led_commands.append(percent)
        if FakeMqtt.acknowledge:
            self.callbacks.on_ack(seq)

    def set_soc_limits(
        self, charge_limit: int, backup_reserve: int, dev_soc: int, seq: int
    ) -> None:
        """Record the command; the system acknowledges it (paho's thread)."""
        self.soc_limit_commands.append((charge_limit, backup_reserve, dev_soc))
        if FakeMqtt.acknowledge:
            self.callbacks.on_ack(seq)


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
    FakeMqtt.acknowledge = True
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
    device = get_device(hass, sn)
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


# -----------------------------
# LED brightness
# -----------------------------

LED_NUMBER_UNIQUE_ID = f"{SN}_led_brightness"
LED_SENSOR_UNIQUE_ID = f"{SN}_JTS1_EMS_CHANGE_REPORT_emsCtrlLedBright"


def _led_entity_id(hass: HomeAssistant) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, LED_NUMBER_UNIQUE_ID
    )


def _led_state(hass: HomeAssistant) -> str:
    entity_id = _led_entity_id(hass)
    assert entity_id
    return hass.states.get(entity_id).state


async def _set_led(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _led_entity_id(hass), "value": value},
        blocking=True,
    )


async def test_led_brightness_only_with_mqtt(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    await _setup(hass, mqtt=False)

    assert _led_entity_id(hass) is None


async def test_led_brightness_removed_when_switched_off(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    assert _led_entity_id(hass)

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "mqtt": False}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _led_entity_id(hass) is None


async def test_led_brightness_entity(hass: HomeAssistant, cloud: AsyncMock) -> None:
    await _setup(hass, mqtt=True)

    entity = er.async_get(hass).async_get(_led_entity_id(hass))
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == "led_brightness"
    assert entity.device_id == (get_device(hass, SN).id)
    state = hass.states.get(entity.entity_id)
    # Current value from the HTTP response
    assert state.state == "10"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 10
    assert state.attributes["unit_of_measurement"] == "%"


async def test_set_led_brightness(hass: HomeAssistant, cloud: AsyncMock) -> None:
    entry = await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    await _set_led(hass, 40)

    assert client.led_commands == [40]
    # Shown right after the acknowledgement, also in the sensor's value
    assert _led_state(hass) == "40"
    assert entry.runtime_data.data[LED_SENSOR_UNIQUE_ID] == 40


async def test_led_brightness_reported_by_the_system(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    # Changed in the app: the system reports it (EMS change report)
    await hass.async_add_executor_job(
        client.callbacks.on_settings, {"emsCtrlLedBright": 60}
    )
    await hass.async_block_till_done()

    assert _led_state(hass) == "60"


async def test_led_brightness_after_unload_is_ignored(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    coordinator = entry.runtime_data
    (client,) = FakeMqtt.instances
    assert await hass.config_entries.async_unload(entry.entry_id)

    await hass.async_add_executor_job(
        client.callbacks.on_settings, {"emsCtrlLedBright": 60}
    )
    await hass.async_block_till_done()

    assert coordinator.data[LED_SENSOR_UNIQUE_ID] == 10


async def test_set_led_brightness_without_acknowledgement(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    await _setup(hass, mqtt=True)
    FakeMqtt.acknowledge = False

    with (
        patch("custom_components.powerocean.coordinator.COMMAND_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError) as error,
    ):
        await _set_led(hass, 40)

    assert error.value.translation_key == "not_acknowledged"
    assert _led_state(hass) == "10"


async def test_led_brightness_unavailable_without_connection(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    cloud.side_effect = EcoflowConnectionError("offline")

    await _setup(hass, mqtt=True)

    assert _led_state(hass) == STATE_UNAVAILABLE


# -----------------------------
# Backup reserve
# -----------------------------

BACKUP_NUMBER_UNIQUE_ID = f"{SN}_backup_reserve"
# Sensor "Discharge limit": the backup reserve of the app
DISCHARGE_LIMIT_UNIQUE_ID = f"{SN}_sysBatDsgDownLimit"


def _backup_entity_id(hass: HomeAssistant) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, BACKUP_NUMBER_UNIQUE_ID
    )


def _backup_state(hass: HomeAssistant) -> str:
    entity_id = _backup_entity_id(hass)
    assert entity_id
    return hass.states.get(entity_id).state


async def _set_backup(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _backup_entity_id(hass), "value": value},
        blocking=True,
    )


async def test_backup_reserve_only_with_mqtt(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    entry = await _setup(hass, mqtt=True)
    assert _backup_entity_id(hass)

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "mqtt": False}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert _backup_entity_id(hass) is None


async def test_backup_reserve_entity(hass: HomeAssistant, cloud: AsyncMock) -> None:
    await _setup(hass, mqtt=True)

    entity = er.async_get(hass).async_get(_backup_entity_id(hass))
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.translation_key == "backup_reserve"
    state = hass.states.get(entity.entity_id)
    # From the EMS change report, not from the 0 directly under "data"
    assert state.state == "5"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 1
    assert state.attributes["unit_of_measurement"] == "%"


async def test_set_backup_reserve(hass: HomeAssistant, cloud: AsyncMock) -> None:
    entry = await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    await _set_backup(hass, 16)

    # Charge limit and devSoc are sent unchanged, as the app does
    assert client.soc_limit_commands == [(100, 16, 100)]
    assert _backup_state(hass) == "16"
    assert entry.runtime_data.data[DISCHARGE_LIMIT_UNIQUE_ID] == 16


async def test_backup_reserve_sends_the_reported_charge_limit(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    # Changed in the app: the system reports both values
    await hass.async_add_executor_job(
        client.callbacks.on_settings,
        {"sysBatChgUpLimit": 90, "sysBatDsgDownLimit": 20},
    )
    await hass.async_block_till_done()
    assert _backup_state(hass) == "20"

    await _set_backup(hass, 10)

    assert client.soc_limit_commands == [(90, 10, 100)]


async def test_backup_reserve_not_sent_without_dev_soc(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    response = copy.deepcopy(RESPONSE)
    del response["data"]["quota"]["JTS1_EMS_PARAM_CHANGE_REPORT"]["devSoc"]
    with patch(
        "custom_components.powerocean.ecoflow.HAEcoflowApi.fetch_raw",
        return_value=response,
    ):
        entry = await _setup(hass, mqtt=True)
    (client,) = FakeMqtt.instances

    assert _backup_state(hass) == STATE_UNAVAILABLE
    with pytest.raises(HomeAssistantError) as error:
        await entry.runtime_data.async_set_backup_reserve(16)

    assert error.value.translation_key == "soc_limits_unknown"
    assert client.soc_limit_commands == []


async def test_set_backup_reserve_without_acknowledgement(
    hass: HomeAssistant, cloud: AsyncMock
) -> None:
    await _setup(hass, mqtt=True)
    FakeMqtt.acknowledge = False

    with (
        patch("custom_components.powerocean.coordinator.COMMAND_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError) as error,
    ):
        await _set_backup(hass, 16)

    assert error.value.translation_key == "not_acknowledged"
    assert _backup_state(hass) == "5"
