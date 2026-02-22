"""test_config_flow."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.powerocean.config_flow import (
    PowerOceanConfigFlow,
    sanitize_device_name,
)
from custom_components.powerocean.const import DOMAIN


@pytest.fixture
def mock_hass():
    return MagicMock(spec=HomeAssistant)


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def flow(mock_hass):
    flow = PowerOceanConfigFlow()
    flow.hass = mock_hass
    flow.context = {"source": "user"}
    return flow


@pytest.fixture
def user_input():
    return {
        "device_id": "12345",
        "email": "test@example.com",
        "password": "password123",
        "model_id": "83",
    }


# -----------------------------
# async_step_user success
# -----------------------------


@patch("custom_components.powerocean.config_flow.validate_input_for_device")
@pytest.mark.asyncio
async def test_async_step_user_success(mock_validate, hass: HomeAssistant, user_input):
    mock_validate.return_value = None

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data=user_input,
    )
    assert result["step_id"] == "device_options"

    mock_validate.assert_called_once()


# -----------------------------
# async_step_user cannot connect
# -----------------------------


@patch("custom_components.powerocean.config_flow.validate_input_for_device")
@pytest.mark.asyncio
async def test_async_step_user_failed(mock_validate, flow, user_input):
    mock_validate.side_effect = HomeAssistantError("cannot_connect")

    result = await flow.async_step_user(user_input)

    assert result["errors"]["base"] == "cannot_connect"


# -----------------------------
# async_step_device_options
# -----------------------------


@pytest.mark.asyncio
async def test_async_step_device_options_create_entry(flow, user_input):
    flow._cloud_data = user_input

    result = await flow.async_step_device_options(
        {
            "friendly_name": "My Device",
            "scan_interval": 20,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"] == user_input
    assert result["options"]["friendly_name"] == "My Device"
    assert result["options"]["scan_interval"] == 20


# -----------------------------
# sanitize_device_name
# -----------------------------


def test_sanitize_device_name():
    assert sanitize_device_name("My # Device  ", "Fallback") == "My Device"
    assert sanitize_device_name("", "Fallback") == "Fallback"
    assert sanitize_device_name("!@#$%", "Fallback") == "Fallback"
    assert len(sanitize_device_name("A" * 300, "Fallback")) == 255
