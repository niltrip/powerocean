"""Helper functions of the parser: serial numbers, nested values, report names."""

from custom_components.powerocean.devices import decode_sn
from custom_components.powerocean.parse import _box_sn, _matches, _nested_value
from custom_components.powerocean.utils import BOX_SCHEMAS, ReportMode

BATTERY_SCHEMA = next(s for s in BOX_SCHEMAS.values() if s["sn_path"] == ["bpSn"])

# -----------------------------
# decode_sn
# -----------------------------


def test_decode_sn_base64() -> None:
    assert decode_sn("U05fVEVTVA==") == "SN_TEST"


def test_decode_sn_plaintext() -> None:
    assert decode_sn("SN_PLAIN") == "SN_PLAIN"


def test_decode_sn_none() -> None:
    assert decode_sn(None) is None


def test_decode_sn_empty_string() -> None:
    assert decode_sn("") is None


# -----------------------------
# _box_sn
# -----------------------------


def test_box_sn_from_payload() -> None:
    assert _box_sn({"bpSn": "U05fVEVTVA=="}, BATTERY_SCHEMA) == "SN_TEST"


def test_box_sn_missing() -> None:
    assert _box_sn({}, BATTERY_SCHEMA) is None


def test_box_sn_invalid() -> None:
    assert _box_sn({"bpSn": 123}, BATTERY_SCHEMA) is None


# -----------------------------
# _nested_value
# -----------------------------


def test_nested_value_success() -> None:
    assert _nested_value({"a": {"b": {"c": 42}}}, ["a", "b", "c"]) == 42


def test_nested_value_missing_key() -> None:
    assert _nested_value({"a": {"b": {}}}, ["a", "b", "c"]) is None


def test_nested_value_wrong_type() -> None:
    assert _nested_value({"a": 123}, ["a", "b"]) is None


# -----------------------------
# _matches
# -----------------------------


def test_energy_stream_valid_keys() -> None:
    assert _matches("RE307_ENERGY_STREAM_REPORT", ReportMode.ENERGY_STREAM.value)
    assert _matches("JTS1_ENERGY_STREAM_REPORT", ReportMode.ENERGY_STREAM.value)


def test_energy_stream_invalid_keys() -> None:
    assert not _matches(
        "RE307_EMS_PV_INV_ENERGY_STREAM_REPORT", ReportMode.ENERGY_STREAM.value
    )


def test_other_reports_simple_match() -> None:
    assert _matches("ABC_BATTERY_REPORT", "BATTERY_REPORT")


def test_key_without_underscore() -> None:
    assert not _matches("updateTime", ReportMode.ENERGY_STREAM.value)
