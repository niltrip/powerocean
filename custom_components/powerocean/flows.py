"""
flows.py: Energy flows between solar, battery, grid and house.

The EMS heartbeat reports solar, grid and battery power. The flows between
them (solar to house, battery to grid, ...) are calculated from the energy
balance: every source is fully split into flows, and the house gets what
is left.

This module does not depend on Home Assistant.
"""

from typing import Any


def clean_zero(v: float, eps: float = 0.05) -> float:
    """
    Setzt kleine Werte auf exakt 0, um Floating-Point Artefakte zu vermeiden.

    Args:
        v (float): Der zu prüfende Wert.
        eps (float, optional): Die Schwelle unterhalb derer der Wert auf 0 gesetzt wird.

    Returns:
        float: 0.0, wenn abs(v) < eps, sonst der Originalwert v.

    Beispiel:
        >>> clean_zero(0.03)
        0.0
        >>> clean_zero(-0.02)
        0.0
        >>> clean_zero(0.1)
        0.1

    """
    if abs(v) < eps:
        return 0.0
    return v


def to_float(value: Any) -> float:
    """Convert a power value to float, treating missing/invalid values as 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def energy_flows(
    solar_total: float, grid_power: Any, battery_power: Any
) -> list[tuple[str, float]]:
    """
    Return the energy flows as (key, value in W), rounded to 0.1 W.

    Args:
        solar_total: Sum of the MPPT power values.
        grid_power: Grid power, positive = import, negative = export.
        battery_power: Battery power, positive = charge, negative = discharge.

    """
    solar = max(float(solar_total), 0.0)
    grid = to_float(grid_power)
    battery = to_float(battery_power)

    battery_charge = max(battery, 0.0)
    battery_discharge = max(-battery, 0.0)

    # House consumption from the physical balance
    house_consumption = max(solar + grid + battery_discharge - battery_charge, 0.0)

    # Solar goes to the house first, then into the battery, the rest to the grid
    solar_to_house = min(solar, house_consumption)
    solar_surplus = solar - solar_to_house
    solar_to_battery = min(solar_surplus, battery_charge)
    solar_to_grid = solar_surplus - solar_to_battery

    # The battery can only supply what solar leaves open, the rest is exported
    battery_to_house = max(
        min(battery_discharge, house_consumption - solar_to_house),
        0.0,
    )
    battery_to_grid = battery_discharge - battery_to_house

    grid_to_battery = battery_charge - solar_to_battery
    grid_to_house = house_consumption - solar_to_house - battery_to_house

    # Numerical safety
    grid_to_house = max(grid_to_house, 0.0)
    grid_to_battery = max(grid_to_battery, 0.0)
    solar_to_grid = max(solar_to_grid, 0.0)
    battery_to_grid = max(battery_to_grid, 0.0)

    flows = [
        ("housePower", house_consumption),
        ("gridPower", grid),
        ("gridToBattery", grid_to_battery),
        ("gridToHouse", grid_to_house),
        ("batteryToHouse", battery_to_house),
        ("batteryToGrid", battery_to_grid),
        ("solarToBattery", solar_to_battery),
        ("solarToGrid", solar_to_grid),
        ("solarToHouse", solar_to_house),
    ]
    return [(key, clean_zero(round(value, 1))) for key, value in flows]
