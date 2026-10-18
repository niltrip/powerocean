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


def test_report_update_time_in_server_time() -> None:
    values = _parse({HEARTBEAT: {"updateTime": "2026-10-18 06:42:15"}})
    # Server time is UTC+8: 2026-10-17 22:42:15 UTC
    assert _value(values, "heartbeatUpdateTime") == 1792276935


def test_report_update_time_invalid() -> None:
    for bad in (None, "", "gestern", 1792276935, "2026-13-40 99:00:00"):
        values = _parse({HEARTBEAT: {"bpRemainWatth": 5000, "updateTime": bad}})
        assert _value(values, "bpRemainWatth") == 5000
        assert _value(values, "heartbeatUpdateTime") is None


def _meter(values: dict[str, Any], key: str) -> Any:
    return values.get(f"{SN}_meter106_{HEARTBEAT}_meterHeartBeat_{key}")


def test_smart_meter_three_phase() -> None:
    meter = {
        "meterAddr": 106,
        "meterType": 11,
        "meterData": [167.5, -6.3, 149.2, 310.4],
    }
    values = _parse({HEARTBEAT: {"meterHeartBeat": [meter]}})
    assert _meter(values, "meterL1Pwr") == 167.5
    assert _meter(values, "meterL2Pwr") == -6.3
    assert _meter(values, "meterL3Pwr") == 149.2
    assert _meter(values, "meterTotalPwr") == 310.4


def test_smart_meter_other_type_is_skipped() -> None:
    meter = {"meterAddr": 106, "meterType": 1, "meterData": [0.0, 0.0, 0.0, -17.0]}
    values = _parse({HEARTBEAT: {"meterHeartBeat": [meter]}})
    assert not [uid for uid in values if "_meter106_" in uid]


def test_smart_meter_invalid_entries() -> None:
    meters = [
        None,
        {"meterType": 11, "meterData": [1.0, 2.0, 3.0, 6.0]},  # no address
        {"meterAddr": 107, "meterType": 11, "meterData": "x"},
        {"meterAddr": 106, "meterType": 11, "meterData": [1.0, None, True]},
    ]
    values = _parse({HEARTBEAT: {"bpRemainWatth": 5000, "meterHeartBeat": meters}})
    assert _value(values, "bpRemainWatth") == 5000
    assert _meter(values, "meterL1Pwr") == 1.0
    assert _meter(values, "meterL2Pwr") is None
    assert _meter(values, "meterL3Pwr") is None
    assert _meter(values, "meterTotalPwr") is None
    assert not [uid for uid in values if "_meter107_" in uid]
