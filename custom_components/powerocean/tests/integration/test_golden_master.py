"""test_golden_master."""

import difflib
import json
from pathlib import Path

import pytest

from custom_components.powerocean.parser import EcoflowParser
from custom_components.powerocean.tests.serialize_structure import (
    serialize_structure,
)
from custom_components.powerocean.tests.utils import normalize

# List of (API response file, variant) pairs
API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
]


@pytest.mark.parametrize("fixture_file_name, variant", API_FIXTURES)
def test_golden_master_parse_values(fixture_file_name, variant) -> None:
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    fixture_file = fixtures_dir / fixture_file_name
    master_file = fixtures_dir / f"golden_master_values_{fixture_file_name}"

    if not fixture_file.exists():
        pytest.skip(f"Fixture file not found: {fixture_file}")

    api_response = json.loads(fixture_file.read_text(encoding="utf-8"))

    parser = EcoflowParser(
        variant=variant,
        sn="SN_INVERTERBOX01",
    )

    # 🔑 DAS ist jetzt die getestete API
    values = parser.parse_values(api_response)

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
def test_golden_master_structure(fixture_file_name, variant) -> None:
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    fixture_file = fixtures_dir / fixture_file_name
    master_file = fixtures_dir / f"golden_master_structure_{fixture_file_name}"

    api_response = json.loads(fixture_file.read_text(encoding="utf-8"))

    parser = EcoflowParser(
        variant=variant,
        sn="SN_INVERTERBOX01",
    )

    structure = parser.parse_structure(api_response)
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
