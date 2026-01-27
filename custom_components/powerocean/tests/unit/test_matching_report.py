# tests/unit/test_matching_report.py
from custom_components.powerocean.const import ReportMode


def test_energy_stream_valid_keys(eco):
    assert eco._is_matching_report(
        "RE307_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )
    assert eco._is_matching_report(
        "JTS1_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )


def test_energy_stream_invalid_keys(eco):
    assert not eco._is_matching_report(
        "RE307_EMS_PV_INV_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )


def test_other_reports_simple_match(eco):
    assert eco._is_matching_report("ABC_BATTERY_REPORT", "BATTERY_REPORT")
