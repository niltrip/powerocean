"""test_golden_master."""

import difflib
import json
from pathlib import Path

import pytest
import requests

from custom_components.powerocean.ecoflow import EcoflowApi
from custom_components.powerocean.tests.serialize_structure import (
    serialize_structure,
)
from custom_components.powerocean.tests.utils import normalize, serialize_sensors

# List of (API response file, variant) pairs
API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
]


# Golden-Master-Test
@pytest.mark.parametrize("fixture_file_name, variant", API_FIXTURES)
def _test_golden_master(fixture_file_name, variant, tmp_path) -> None:
    """Regression test: compare current sensor extraction to golden master for multiple responses/variants."""
    fixture_file = Path(__file__).parent.parent / "fixtures" / fixture_file_name
    # Golden Master path (per response)
    master_file = fixture_file.parent / f"golden_master_{fixture_file_name}"

    if not fixture_file.exists():
        pytest.skip(f"Fixture file not found: {fixture_file}")

    with fixture_file.open("r", encoding="utf-8") as f:
        api_response = json.load(f)

    # --- Ecoflow Dummy Init ---
    serialnumber = "SN_INVERTERBOX01"
    username = "dummy_user"
    password = "dummy_pass"

    eco = Ecoflow(
        serialnumber=serialnumber,
        username=username,
        password=password,
        variant=variant,
        options={},
    )

    # Zusätzliche Felder
    eco.sn_inverter = serialnumber
    eco.token = "dummy_token"
    eco.device = None
    eco.session = requests.Session()
    eco.url_iot_app = "https://api.ecoflow.com/auth/login"
    eco.url_user_fetch = "https://dummy.url"
    eco.datapointfile = (
        Path(__file__).parent.parent.parent / "variants" / f"{variant}.json"
    )

    # --- Parser ausführen ---
    sensors = eco._get_sensors(api_response)

    # --- Sensoren serialisieren ---
    serialized = serialize_sensors(sensors)
    # sort keys deterministisch
    serialized = dict(sorted(serialized.items()))

    # --- Wenn Golden Master noch nicht existiert, erstellen ---
    if not master_file.exists():
        with master_file.open("w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2, sort_keys=True)
        pytest.skip(
            f"Golden Master created for {fixture_file_name}. Re-run test to validate."
        )

    # --- Vergleich mit Golden Master ---
    with master_file.open("r", encoding="utf-8") as f:
        golden_master = json.load(f)

        # --- Diff ---
    if serialized != golden_master:
        pytest.fail(
            f"Sensors differ from Golden Master for {fixture_file_name}.\n"
            f"Run `git diff {master_file}` to inspect changes."
        )


@pytest.mark.parametrize("fixture_file_name, variant", API_FIXTURES)
def test_golden_master_parse_values(fixture_file_name, variant):
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    fixture_file = fixtures_dir / fixture_file_name
    master_file = fixtures_dir / f"golden_master_values_{fixture_file_name}"

    if not fixture_file.exists():
        pytest.skip(f"Fixture file not found: {fixture_file}")

    api_response = json.loads(fixture_file.read_text(encoding="utf-8"))

    eco = EcoflowApi(
        hass=None,  # nicht benötigt
        serialnumber="SN_INVERTERBOX01",
        username="dummy",
        password="dummy",
        variant=variant,
    )

    # 🔑 DAS ist jetzt die getestete API
    values = eco.parse_values(api_response)

    # deterministische Reihenfolge
    values = dict(sorted(values.items()))

    # Golden Master erzeugen (erster Lauf)
    if not master_file.exists():
        master_file.write_text(
            json.dumps(values, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        pytest.skip("Golden master created – re-run test")

    golden_master = json.loads(master_file.read_text(encoding="utf-8"))

    assert values == golden_master


@pytest.mark.parametrize("fixture_file_name, variant", API_FIXTURES)
def test_golden_master_structure(fixture_file_name, variant):
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    fixture_file = fixtures_dir / fixture_file_name
    master_file = fixtures_dir / f"golden_master_structure_{fixture_file_name}"

    api_response = json.loads(fixture_file.read_text(encoding="utf-8"))

    eco = EcoflowApi(
        hass=None,
        serialnumber="SN_INVERTERBOX01",
        username="dummy",
        password="dummy",
        variant=variant,
    )

    structure = eco.parse_structure(api_response)
    serialized = serialize_structure(structure)

    if not master_file.exists():
        master_file.write_text(
            json.dumps(normalize(serialized), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        pytest.skip("Golden master created – re-run test")

    golden_master = json.loads(master_file.read_text(encoding="utf-8"))

    a = json.dumps(serialized, indent=2, sort_keys=True)
    b = json.dumps(golden_master, indent=2, sort_keys=True)

    for line in difflib.unified_diff(a.splitlines(), b.splitlines()):
        print(line)

    assert normalize(serialized) == golden_master
