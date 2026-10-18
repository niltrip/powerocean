"""Anonymized API responses keep the device structure and parse like the original."""

import base64
import json
from pathlib import Path

import pytest

from custom_components.powerocean.anonymize import REDACT_KEYS, REDACTED, Anonymizer
from custom_components.powerocean.parser import EcoflowParser

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
# (API response file, variant), as in the golden master tests
API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
]
MAIN_SN = "SN_INVERTERBOX01"


def _load(file_name: str) -> dict:
    return json.loads((FIXTURES_DIR / file_name).read_text("utf-8"))


@pytest.mark.parametrize("fixture_file_name", [f for f, _ in API_FIXTURES])
def test_serial_numbers_are_replaced(fixture_file_name: str) -> None:
    anonymizer = Anonymizer(MAIN_SN)
    dumped = json.dumps(anonymizer.anonymize(_load(fixture_file_name)))

    assert anonymizer.aliases[MAIN_SN] == "SN_MAIN"
    for sn in anonymizer.aliases:
        assert sn not in dumped
        assert base64.b64encode(sn.encode()).decode() not in dumped


@pytest.mark.parametrize(("fixture_file_name", "variant"), API_FIXTURES)
def test_parser_result_is_unchanged(fixture_file_name: str, variant: str) -> None:
    response = _load(fixture_file_name)
    anonymizer = Anonymizer(MAIN_SN)
    anonymized = anonymizer.anonymize(response)
    parser = EcoflowParser(variant=variant, sn=MAIN_SN)
    anon_parser = EcoflowParser(variant=variant, sn="SN_MAIN")

    structure = parser.parse_structure(response)
    anon_structure = anon_parser.parse_structure(anonymized)
    assert {anonymizer.anonymize(uid) for uid in structure} == set(anon_structure)

    values = parser.parse_values(response)
    anon_values = anon_parser.parse_values(anonymized)
    for uid, value in values.items():
        anon_uid = anonymizer.anonymize(uid)
        if any(uid.endswith(f"_{key}") for key in REDACT_KEYS):
            assert anon_values[anon_uid] in (REDACTED, "")
        else:
            assert anon_values[anon_uid] == anonymizer.anonymize(value)


def test_structure_hints_and_encodings() -> None:
    battery_sn = base64.b64encode(b"BATTERY000001").decode()
    response = {
        "data": {
            "systemName": "Mein Haus",
            "timezone": "Europe/Berlin",
            "quota": {
                "JTS1_BP_STA_REPORT": {
                    "BATTERY000001": json.dumps({"bpSn": battery_sn, "bpSoc": 50}),
                    "updateTime": "2026-01-20 05:18:05",
                },
                "JTS1_ERROR_CHANGE_REPORT": {
                    "bpErrCode": [{"errCode": [], "moduleSn": "BATTERY000001"}],
                },
            },
        }
    }

    result = Anonymizer().anonymize(response)
    quota = result["data"]["quota"]
    payload = json.loads(quota["JTS1_BP_STA_REPORT"]["SN_BP01"])

    assert result["data"]["systemName"] == REDACTED
    assert result["data"]["timezone"] == REDACTED
    assert payload == {"bpSn": base64.b64encode(b"SN_BP01").decode(), "bpSoc": 50}
    assert quota["JTS1_BP_STA_REPORT"]["updateTime"] == "2026-01-20 05:18:05"
    assert quota["JTS1_ERROR_CHANGE_REPORT"]["bpErrCode"][0]["moduleSn"] == "SN_BP01"
