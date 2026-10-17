"""test_config_flow."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.api import (
    AuthenticationError,
    EcoflowConnectionError,
    RegionDetectionError,
)
from custom_components.powerocean.config_flow import sanitize_device_name
from custom_components.powerocean.const import DOMAIN

AUTHORIZE = "custom_components.powerocean.config_flow.HAEcoflowApi.async_authorize"

USER_INPUT = {
    "device_id": "12345",
    "email": "test@example.com",
    "password": "password123",
    "model_id": "83",
}


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent the real integration setup when a flow creates or reloads an entry."""
    with patch(
        "custom_components.powerocean.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
def mock_authorize() -> Generator[AsyncMock]:
    with patch(AUTHORIZE) as mock:
        yield mock


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="PowerOcean 12345",
        title="PowerOcean",
        data=USER_INPUT,
        options={"friendly_name": "PowerOcean", "scan_interval": 10},
    )


# -----------------------------
# user flow
# -----------------------------


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"friendly_name": "My # Device", "scan_interval": 20}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PowerOcean"
    assert result["data"] == USER_INPUT
    assert result["options"] == {"friendly_name": "My Device", "scan_interval": 20}
    assert result["result"].unique_id == "PowerOcean 12345"
    mock_authorize.assert_awaited_once()


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (AuthenticationError, "invalid_auth"),
        (EcoflowConnectionError, "cannot_connect"),
        (RegionDetectionError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    mock_authorize: AsyncMock,
    mock_setup_entry: AsyncMock,
    side_effect: type[Exception],
    error: str,
) -> None:
    mock_authorize.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}

    # User fixes the problem and continues
    mock_authorize.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "device_options"


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_authorize: AsyncMock
) -> None:
    _entry().add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_authorize.assert_not_awaited()


# -----------------------------
# reconfigure flow
# -----------------------------


async def test_reconfigure_updates_entry(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "password": "new-password"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["password"] == "new-password"
    assert entry.unique_id == "PowerOcean 12345"


async def test_reconfigure_invalid_auth_shows_error(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Regression: wrong credentials used to end in an unhandled error."""
    entry = _entry()
    entry.add_to_hass(hass)
    mock_authorize.side_effect = AuthenticationError

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "password": "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["password"] == "password123"


async def test_reconfigure_other_device_aborts(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """All entity unique IDs contain the serial, so it must not change."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "device_id": "67890"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data["device_id"] == "12345"
    mock_authorize.assert_not_awaited()


async def test_reconfigure_legacy_unique_id(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    """Entries from older versions use another unique ID format."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="PowerOcean_12345",
        data=USER_INPUT,
        options={"friendly_name": "PowerOcean", "scan_interval": 10},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "model_id": "87"}
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["model_id"] == "87"
    assert entry.unique_id == "PowerOcean_12345"


# -----------------------------
# reauth flow
# -----------------------------


async def test_reauth_flow(
    hass: HomeAssistant, mock_authorize: AsyncMock, mock_setup_entry: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_authorize.side_effect = AuthenticationError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "test@example.com", "password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_authorize.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "test@example.com", "password": "new-password"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-password"
    assert entry.data["device_id"] == "12345"


# -----------------------------
# options flow
# -----------------------------


async def test_options_flow_updates_and_reloads(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert mock_setup_entry.call_count == 1

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"friendly_name": "Keller!", "scan_interval": 30}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"friendly_name": "Keller", "scan_interval": 30}
    # OptionsFlowWithReload reloads the entry so the new interval is applied
    assert mock_setup_entry.call_count == 2


# -----------------------------
# sanitize_device_name
# -----------------------------


def test_sanitize_device_name():
    assert sanitize_device_name("My # Device  ", "Fallback") == "My Device"
    assert sanitize_device_name("", "Fallback") == "Fallback"
    assert sanitize_device_name("!@#$%", "Fallback") == "Fallback"
    assert len(sanitize_device_name("A" * 300, "Fallback")) == 255
