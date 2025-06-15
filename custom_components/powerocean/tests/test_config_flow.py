from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerocean.const import DOMAIN

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001, ANN201
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_valid_input():  # noqa: ANN201
    """Mock successful input."""
    return {
        "device_id": "Device123",
        "email": "userd123@aol.com",
        "password": "passwortd123@aol.com",
        "model_id": "83",
    }


@pytest.fixture
def mock_device_info():  # noqa: ANN201, D103
    return {
        "serial": "Device123",
        "product": "PowerOcean",
        "name": "MyDevice",
    }


@pytest.mark.asyncio
async def test_user_step_successful(
    hass: HomeAssistant, mock_valid_input: dict, mock_device_info: dict
) -> None:
    """Test a successful config flow."""
    with (
        patch("custom_components.powerocean.config_flow.Ecoflow.authorize"),
        patch(
            "custom_components.powerocean.config_flow.Ecoflow.get_device",
            return_value=mock_device_info,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

        assert result["type"] == FlowResultType.FORM  # type: ignore  # noqa: S101
        assert result["step_id"] == "user"  # type: ignore  # noqa: S101

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=mock_valid_input
        )

        assert result2["type"] == FlowResultType.FORM  # type: ignore  # noqa: S101
        assert result2["step_id"] == "device_options"  # type: ignore  # noqa: S101

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            user_input={"friendly_name": "My PowerOcean", "scan_interval": 15},
        )

        assert result3["type"] == FlowResultType.CREATE_ENTRY  # type: ignore  # noqa: S101
        assert result3["title"] == "My PowerOcean"  # type: ignore  # noqa: S101
        assert result3["data"]["user_input"] == mock_valid_input  # type: ignore  # noqa: S101


@pytest.mark.asyncio
async def test_user_step_invalid_auth(hass: HomeAssistant, mock_valid_input) -> None:  # noqa: ANN001
    """Test config flow with invalid credentials."""
    with patch(
        "custom_components.powerocean.config_flow.Ecoflow.authorize",
        side_effect=Exception("AuthFail"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=mock_valid_input
        )

        assert result2["type"] == FlowResultType.FORM  # type: ignore  # noqa: S101
        assert result2["errors"]["base"] == "unknown"  # type: ignore  # noqa: S101
