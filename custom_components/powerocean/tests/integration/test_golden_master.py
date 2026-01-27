# tests/test_golden_master.py
import json
from pathlib import Path

import pytest
import requests

from custom_components.powerocean.ecoflow import Ecoflow
from custom_components.powerocean.tests.utils import serialize_sensors

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
def test_golden_master(fixture_file_name, variant, tmp_path) -> None:
    """Regression test: compare current sensor extraction to golden master for multiple responses/variants."""
    fixture_file = Path(__file__).parent / "fixtures" / fixture_file_name
    if not fixture_file.exists():
        pytest.skip(f"Fixture file not found: {fixture_file}")

    with fixture_file.open("r", encoding="utf-8") as f:
        api_response = json.load(f)

    # Golden Master path (per response)
    master_file = fixture_file.parent / f"golden_master_{fixture_file_name}"

    # --- Ecoflow Dummy Init ---
    serialnumber = "SN_INVERTERBOX01"
    username = "dummy_user"
    password = "dummy_pass"
    options = None

    eco = Ecoflow(
        serialnumber=serialnumber,
        username=username,
        password=password,
        variant=variant,
        options=options,
    )

    # Zusätzliche Felder
    eco.sn_inverter = serialnumber
    eco.token = "dummy_token"
    eco.device = None
    eco.session = requests.Session()
    eco.url_iot_app = "https://api.ecoflow.com/auth/login"
    eco.url_user_fetch = "https://dummy.url"
    base_path = Path(__file__).parent.parent
    eco.datapointfile = base_path / "variants" / f"{variant}.json"

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
