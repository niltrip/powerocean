"""test_soc_limits: charge limit, backup reserve and devSoc from the reports."""

import json
from pathlib import Path

import pytest

from custom_components.powerocean.coordinator import soc_limits_of

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _response(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text("utf-8"))


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        # devSoc from the EMS parameter change report
        (
            "response_modified.json",
            {"sysBatChgUpLimit": 100, "sysBatDsgDownLimit": 5, "devSoc": 100},
        ),
        # PowerOcean Plus (issue #196): no parameter change report, devSoc in
        # the EMS change report, no charge limit at all
        (
            "response_modified_po_plus_feature.json",
            {"sysBatDsgDownLimit": 10, "devSoc": 100},
        ),
        ("response_modified_po_plus.json", {"sysBatDsgDownLimit": 0, "devSoc": 30}),
    ],
)
def test_soc_limits_of(fixture: str, expected: dict[str, int]) -> None:
    assert soc_limits_of(_response(fixture)) == expected


def test_soc_limits_without_quota() -> None:
    assert soc_limits_of({"data": {}}) == {}
