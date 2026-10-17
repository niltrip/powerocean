"""test_diagnostics: anonymized diagnostics download."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.const import DOMAIN
from custom_components.powerocean.diagnostics import (
    async_get_config_entry_diagnostics,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
RAW_RESPONSE = (FIXTURES_DIR / "response_modified.json").read_text("utf-8")

SN = "SN_INVERTERBOX01"
DATA = {
    "device_id": SN,
    "email": "test@example.com",
    "password": "password123",
    "model_id": "83",
}


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


async def test_diagnostics(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=2,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data=DATA,
        options={"friendly_name": "PowerOcean", "scan_interval": 10},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.async_authorize",
            return_value=None,
        ),
        patch(
            "custom_components.powerocean.ecoflow.HAEcoflowApi.fetch_raw",
            return_value=json.loads(RAW_RESPONSE),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diagnostics)

    # No credentials, no serial numbers
    for secret in ("test@example.com", "password123", "SN_BATTERIEPACK01", SN):
        assert secret not in dumped
    assert diagnostics["entry"]["data"]["email"] == "**REDACTED**"

    # The raw response is included, with placeholders
    quota = diagnostics["api_response"]["data"]["quota"]
    assert set(quota) == set(json.loads(RAW_RESPONSE)["data"]["quota"])
    assert diagnostics["api_response"]["data"]["systemName"] == "**REDACTED**"

    # Devices with their structure and current values
    devices = diagnostics["devices"]
    assert devices["SN_MAIN"]["datapoints"]["JTS1_EMS_CHANGE_REPORT_bpSoc"] == 5
    batteries = [d for d in devices.values() if d["model"] == "PowerOcean Battery"]
    assert len(batteries) == 2
    assert all(b["via_device"] == "SN_MAIN" for b in batteries)
    assert diagnostics["coordinator"]["last_update_success"] is True
