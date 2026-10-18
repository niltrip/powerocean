"""test_energy_flows."""

import json
from pathlib import Path

import pytest

from custom_components.powerocean.tests.utils import parse_values

FLOW_KEYS = (
    "housePower",
    "gridToHouse",
    "gridToBattery",
    "batteryToHouse",
    "batteryToGrid",
    "solarToHouse",
    "solarToBattery",
    "solarToGrid",
)

# Same fixtures as the golden master test, one entry per supported variant.
API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
]


def _build_response(solar: float, grid: float, battery: float) -> dict:
    """Build a minimal EMS heartbeat response for a single inverter."""
    return {
        "data": {
            "quota": {
                "JTS1_EMS_HEARTBEAT": {
                    "pcsMeterPower": grid,
                    "emsBpPower": battery,
                    "mpptHeartBeat": [{"mpptPv": [{"pwr": solar}]}],
                }
            }
        }
    }


def _flows_per_inverter(values: dict) -> list[dict[str, float]]:
    """Group the calculated flow values by inverter prefix."""
    grouped: dict[str, dict[str, float]] = {}
    for unique_id, value in values.items():
        for key in FLOW_KEYS:
            if unique_id.endswith(f"_{key}"):
                prefix = unique_id[: -len(key) - 1]
                grouped.setdefault(prefix, {})[key] = float(value)
    return list(grouped.values())


def test_house_inflow_matches_house_consumption() -> None:
    """Solar, battery and grid feeding the house must add up to housePower."""
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    for fixture_file_name, variant in API_FIXTURES:
        response = json.loads(
            (fixtures_dir / fixture_file_name).read_text(encoding="utf-8")
        )
        for flows in _flows_per_inverter(parse_values(response, variant)):
            inflow = (
                flows.get("solarToHouse", 0.0)
                + flows.get("batteryToHouse", 0.0)
                + flows.get("gridToHouse", 0.0)
            )
            assert inflow == pytest.approx(flows["housePower"], abs=0.2), (
                f"{fixture_file_name}: house inflow {inflow} != "
                f"housePower {flows['housePower']}"
            )
            for key, value in flows.items():
                assert value >= 0.0, f"{fixture_file_name}: {key} is negative"


def test_battery_export_is_not_counted_as_house_supply() -> None:
    """
    Discharging into the grid must not show up as batteryToHouse.

    Values measured on a live system: 1900 W solar, 2787 W exported,
    2205 W supplied by the battery, leaving 1318 W for the house.
    """
    values = parse_values(_build_response(1900.0, -2787.0, -2205.0), "83")
    flows = _flows_per_inverter(values)[0]

    assert flows["housePower"] == pytest.approx(1318.0, abs=0.2)
    assert flows["solarToHouse"] == pytest.approx(1318.0, abs=0.2)
    assert flows["solarToGrid"] == pytest.approx(582.0, abs=0.2)
    assert flows["batteryToHouse"] == pytest.approx(0.0, abs=0.2)
    assert flows.get("batteryToGrid", 0.0) == pytest.approx(2205.0, abs=0.2)
    assert flows["gridToHouse"] == pytest.approx(0.0, abs=0.2)
    assert flows["gridToBattery"] == pytest.approx(0.0, abs=0.2)


# Solar, grid (+ import / - export) and battery (+ charge / - discharge) in W
SOLAR = (0.0, 100.0, 500.0, 1318.0, 1900.0, 3000.0)
GRID = (-3000.0, -2787.0, -500.0, 0.0, 500.0, 2000.0)
BATTERY = (-2205.0, -1000.0, -300.0, 0.0, 300.0, 1500.0)
BALANCE_SCENARIOS = [
    (solar, grid, battery)
    for solar in SOLAR
    for grid in GRID
    for battery in BATTERY
    # The house can't feed power back
    if solar + grid - battery >= 0
]


@pytest.mark.parametrize(("solar", "grid", "battery"), BALANCE_SCENARIOS)
def test_energy_balance(solar: float, grid: float, battery: float) -> None:
    """Every source and every sink must be fully split into flows."""
    values = parse_values(_build_response(solar, grid, battery), "83")
    flows = _flows_per_inverter(values)[0]

    grid_import, grid_export = max(grid, 0.0), max(-grid, 0.0)
    charge, discharge = max(battery, 0.0), max(-battery, 0.0)
    balance = {
        "house": (
            flows["solarToHouse"] + flows["batteryToHouse"] + flows["gridToHouse"],
            solar + grid - battery,
        ),
        "solar": (
            flows["solarToHouse"] + flows["solarToBattery"] + flows["solarToGrid"],
            solar,
        ),
        "discharge": (flows["batteryToHouse"] + flows["batteryToGrid"], discharge),
        "charge": (flows["solarToBattery"] + flows["gridToBattery"], charge),
        "export": (flows["solarToGrid"] + flows["batteryToGrid"], grid_export),
        "import": (flows["gridToHouse"] + flows["gridToBattery"], grid_import),
    }

    assert flows["housePower"] == pytest.approx(solar + grid - battery, abs=0.2)
    for name, (total, expected) in balance.items():
        assert total == pytest.approx(expected, abs=0.2), name
    for key, value in flows.items():
        assert value >= 0.0, f"{key} is negative"
