"""
Golden master for everything users see in Home Assistant.

Sets up the integration with each API fixture and snapshots the resulting
entities and devices: entity_id, unique_id, name, device, classes, unit,
icon, state and attributes. Automations and dashboards depend on these,
so any diff here is a breaking change for existing installations and must
be intentional.

Delete a golden master file and re-run to regenerate it.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerocean.const import DOMAIN

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SN = "SN_INVERTERBOX01"

API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
    ("response_modified_error_521.json", "83"),
    # Synthetic: heating rod (no real response available yet)
    ("response_synthetic_heating_rod.json", "83"),
    ("response_synthetic_heating_rod_dual.json", "83"),
]


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


def _snapshot(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entities = {}
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        state = hass.states.get(entity.entity_id)
        device = device_registry.async_get(entity.device_id)
        attributes = dict(state.attributes) if state else {}
        entities[entity.unique_id] = {
            "entity_id": entity.entity_id,
            "original_name": entity.original_name,
            "device": device.name if device else None,
            "device_class": entity.original_device_class,
            "unit_of_measurement": entity.unit_of_measurement,
            "state_class": attributes.get("state_class"),
            "entity_category": entity.entity_category,
            "icon": entity.original_icon,
            "state": state.state if state else None,
            "product_description": attributes.get("product_description"),
            "product_serial": attributes.get("product_serial"),
        }

    devices = {}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        via = (
            device_registry.async_get(device.via_device_id)
            if device.via_device_id
            else None
        )
        devices[device.serial_number or device.id] = {
            "identifiers": sorted(list(i) for i in device.identifiers),
            "name": device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "model_id": device.model_id,
            "via_device": via.serial_number if via else None,
        }

    return json.loads(
        json.dumps(
            {"entities": entities, "devices": devices}, sort_keys=True, default=str
        )
    )


@pytest.mark.parametrize(("fixture_file_name", "variant"), API_FIXTURES)
async def test_entities_golden_master(
    hass: HomeAssistant, fixture_file_name: str, variant: str
) -> None:
    master_file = FIXTURES_DIR / f"golden_master_entities_{fixture_file_name}"
    response = json.loads((FIXTURES_DIR / fixture_file_name).read_text("utf-8"))

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=f"PowerOcean {SN}",
        title="PowerOcean",
        data={
            "device_id": SN,
            "email": "test@example.com",
            "password": "password123",
            "model_id": variant,
        },
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
            return_value=response,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    snapshot = _snapshot(hass, entry)
    assert snapshot["entities"], "no entities were created"

    if not master_file.exists():
        master_file.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        pytest.skip("Golden master created - re-run test")

    golden_master = json.loads(master_file.read_text("utf-8"))

    assert snapshot["devices"] == golden_master["devices"]
    assert sorted(snapshot["entities"]) == sorted(golden_master["entities"]), (
        "entities were added or removed (unique_id changed?)"
    )
    for unique_id, expected in golden_master["entities"].items():
        assert snapshot["entities"][unique_id] == expected, unique_id
