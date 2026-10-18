"""
test_parse_robustness: incomplete or unexpected API payloads.

A single malformed field must not abort the whole update (which would make
every sensor unavailable). Valid payloads are covered by the golden masters.
"""

from typing import Any

import pytest

from custom_components.powerocean.tests.utils import parse_values

SN = "SN_INVERTERBOX01"
HEARTBEAT = "JTS1_EMS_HEARTBEAT"


def _parse(quota: dict[str, Any]) -> dict[str, Any]:
    return parse_values({"data": {"quota": quota}}, "83", SN)


def _value(values: dict[str, Any], key: str) -> Any:
    return values.get(f"{SN}_{HEARTBEAT}_{key}")


def _flow(values: dict[str, Any], key: str) -> Any:
    return values.get(f"{SN}_{HEARTBEAT}_mpptHeartBeat_{key}")


def test_quota_key_without_underscore() -> None:
    values = _parse({"updateTime": 1, HEARTBEAT: {"bpRemainWatth": 5000}})
    assert _value(values, "bpRemainWatth") == 5000


def test_heartbeat_empty_mppt_list() -> None:
    values = _parse({HEARTBEAT: {"bpRemainWatth": 5000, "mpptHeartBeat": []}})
    assert _value(values, "bpRemainWatth") == 5000


def test_heartbeat_mppt_without_pv_list() -> None:
    values = _parse(
        {HEARTBEAT: {"pcsMeterPower": 300, "mpptHeartBeat": [{"mpptInsResist": 5}]}}
    )
    assert _flow(values, "mpptPv_pwrTotal") == 0
    assert _flow(values, "mpptInsResist") == 5
    assert _flow(values, "gridToHouse") == 300.0


def test_heartbeat_invalid_mppt_entries() -> None:
    values = _parse(
        {
            HEARTBEAT: {
                "mpptHeartBeat": [
                    {"mpptPv": [{"pwr": 100}, None, {"pwr": None}, {"pwr": 50}]}
                ]
            }
        }
    )
    total = _flow(values, "mpptPv_pwrTotal")
    assert total == 150
    assert isinstance(total, int)  # same type as before for valid data


def test_heartbeat_missing_power_values() -> None:
    values = _parse(
        {
            HEARTBEAT: {
                "pcsMeterPower": None,
                "emsBpPower": "n/a",
                "mpptHeartBeat": [{"mpptPv": [{"pwr": 400}]}],
            }
        }
    )
    assert _flow(values, "solarToHouse") == 400.0
    assert _flow(values, "gridToHouse") == 0.0


def test_heartbeat_phase_not_a_dict() -> None:
    values = _parse(
        {
            HEARTBEAT: {
                "bpRemainWatth": 5000,
                "pcsAPhase": None,
                "pcsBPhase": {"vol": 230},
                "pcsCPhase": {"vol": 231},
            }
        }
    )
    assert _value(values, "bpRemainWatth") == 5000
    assert _value(values, "pcsBPhase_vol") == 230


def test_parallel_energy_stream_invalid_entry() -> None:
    response = {
        "data": {
            "parallel": {
                SN: {
                    "JTS1_PARALLEL_ENERGY_STREAM_REPORT": {
                        "paraEnergyStream": [None, {"gridPwr": 10}]
                    }
                }
            }
        }
    }
    values = parse_values(response, "83", SN)
    assert (
        values[f"{SN}_all_JTS1_PARALLEL_ENERGY_STREAM_REPORT_paraEnergyStream_gridPwr"]
        == 10
    )


def test_unknown_variant_fails() -> None:
    # The coordinator turns this into a failed update
    with pytest.raises(ValueError, match="default"):
        parse_values({"data": {"quota": {}}}, "default", SN)
