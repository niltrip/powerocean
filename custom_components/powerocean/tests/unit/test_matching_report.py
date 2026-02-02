"""test_matching_report."""

from custom_components.powerocean.utils import ReportMode


def test_energy_stream_valid_keys(eco) -> None:
    assert eco._is_matching_report(
        "RE307_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )
    assert eco._is_matching_report(
        "JTS1_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )


def test_energy_stream_invalid_keys(eco) -> None:
    assert not eco._is_matching_report(
        "RE307_EMS_PV_INV_ENERGY_STREAM_REPORT",
        ReportMode.ENERGY_STREAM.value,
    )


def test_other_reports_simple_match(eco) -> None:
    assert eco._is_matching_report("ABC_BATTERY_REPORT", "BATTERY_REPORT")
