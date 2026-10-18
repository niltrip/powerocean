"""test_errors: active errors from the error report."""

import base64
import json
from pathlib import Path

from custom_components.powerocean.errors import ActiveError, parse_error_report

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text("utf-8"))


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_real_report_with_base64_module_sn() -> None:
    report = _load("response_modified_error_521.json")["data"]["quota"][
        "JTS1_ERROR_CHANGE_REPORT"
    ]

    assert parse_error_report(report) == [
        ActiveError(
            module="ems",
            module_sn="SN_INVERTERBOX01",
            code=521,
            name="Energy storage communication error",
            level="Warning",
        )
    ]


def test_report_with_plain_module_sn() -> None:
    report = _load("response_modified_dcfit_2025.json")["data"]["quota"][
        "JTS1_ERROR_CHANGE_REPORT"
    ]

    assert parse_error_report(report) == [
        ActiveError(module="ems", module_sn="SystemSeriennummer", code=503),
        ActiveError(module="pcs", module_sn="Speichercontroller", code=37),
    ]


def test_report_without_errors() -> None:
    report = _load("response_modified.json")["data"]["quota"][
        "JTS1_ERROR_CHANGE_REPORT"
    ]

    assert parse_error_report(report) == []


def test_battery_errors_and_unusual_values() -> None:
    report = {
        "bpErrCode": [
            {"errCode": [101, "102"], "moduleSn": _b64("SN_BP01")},
            {"errCode": [], "moduleSn": _b64("SN_BP02")},
            {"errCode": [True, None, "x"], "moduleSn": _b64("SN_BP03")},
            "not a module",
        ],
        "pcsErrCode": {"errCode": None, "moduleSn": "SN_PCS"},
        "emsErrCode": [],
        "updateTime": "2026-10-18 06:38:07",
    }

    assert parse_error_report(report) == [
        ActiveError(module="bp", module_sn="SN_BP01", code=101),
        ActiveError(module="bp", module_sn="SN_BP01", code=102),
    ]


def test_known_codes_depend_on_device() -> None:
    report = {
        "emsErrCode": {"errCode": [702], "moduleSn": "SN_MAIN"},
        "bpErrCode": [{"errCode": [203, 702], "moduleSn": "SN_BP01"}],
    }

    errors = parse_error_report(report)

    assert [error.as_dict() for error in errors] == [
        {
            "module": "ems",
            "module_sn": "SN_MAIN",
            "code": 702,
            "name": "Low power mode",
            "level": "Info",
        },
        {
            "module": "bp",
            "module_sn": "SN_BP01",
            "code": 203,
            "name": "BP module power-on notification",
            "level": "Info",
        },
        # 702 is only known for the inverter
        {"module": "bp", "module_sn": "SN_BP01", "code": 702},
    ]
