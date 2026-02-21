"""
types.py: Definitions for PowerOcean endpoints and sensor metadata.

This module defines dataclasses and helpers for working with PowerOcean
devices in Home Assistant. It includes:

- `PowerOceanEndPoint`: Represents an endpoint (sensor or value) of a
  PowerOcean device with associated metadata and current value.
- `SensorClassHelper`: Infers sensor class, unit, and state class from
  sensor key names.
- `SensorMetaHelper`: Provides metadata such as descriptions and icons
  for sensors based on key semantics.

These types are used throughout the integration to standardize sensor
handling and presentation in Home Assistant.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, TypeAlias

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.helpers.device_registry import DeviceInfo

SensorClassTuple: TypeAlias = tuple[
    SensorDeviceClass | None,
    str | None,
    SensorStateClass | None,
]


@dataclass
class PowerOceanEndPoint:
    """
    Represents a PowerOcean endpoint with metadata and value.

    Attributes:
        internal_unique_id (str): Unique identifier for the endpoint.
        serial (str): Serial number of the device.
        name (str): Name of the endpoint.
        friendly_name (str): Human-readable name.
        value (str | int | float | None): Value of the endpoint.
        cls: (SensorClassTuple | None): Unit of measurement.
        description (str): Description of the endpoint.
        icon (str | None): Icon representing the endpoint.
        device_info (DeviceInfo): Inverter/Battery/Wallbox.

    """

    internal_unique_id: str
    serial: str
    name: str
    friendly_name: str
    value: str | int | float | None
    cls: SensorClassTuple | None
    description: str
    icon: str | None
    device_info: DeviceInfo | None = None


class SensorClassHelper:
    """Infer SensorDeviceClass, unit and SensorStateClass from a sensor key."""

    _CLASS_PATTERNS: ClassVar[list[tuple[re.Pattern[str], SensorClassTuple]]] = [
        (
            re.compile(
                r"(pwr|power|pwrTotal|grid|bat|pv|battery|house)$", re.IGNORECASE
            ),
            (
                SensorDeviceClass.POWER,
                UnitOfPower.WATT,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        (
            re.compile(r"(amp|current)$", re.IGNORECASE),
            (
                SensorDeviceClass.CURRENT,
                UnitOfElectricCurrent.AMPERE,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        (
            re.compile(r"(vol|voltage)$", re.IGNORECASE),
            (
                SensorDeviceClass.VOLTAGE,
                UnitOfElectricPotential.VOLT,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        (
            re.compile(r"(watth|dayenergy)$", re.IGNORECASE),
            (
                SensorDeviceClass.ENERGY,
                UnitOfEnergy.WATT_HOUR,
                SensorStateClass.TOTAL,
            ),
        ),
        (
            re.compile(r"(energy)$", re.IGNORECASE),
            (
                SensorDeviceClass.ENERGY,
                UnitOfEnergy.WATT_HOUR,
                SensorStateClass.TOTAL_INCREASING,
            ),
        ),
        (
            re.compile(r"(ElectricityGeneration)$", re.IGNORECASE),
            (
                SensorDeviceClass.ENERGY,
                UnitOfEnergy.KILO_WATT_HOUR,
                SensorStateClass.TOTAL_INCREASING,
            ),
        ),
        (
            re.compile(r"(soc|soh|percent)$", re.IGNORECASE),
            (
                SensorDeviceClass.BATTERY,
                PERCENTAGE,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        (
            re.compile(r"(temp|temperature)$", re.IGNORECASE),
            (
                SensorDeviceClass.TEMPERATURE,
                UnitOfTemperature.CELSIUS,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        (
            re.compile(r"volume", re.IGNORECASE),
            (
                SensorDeviceClass.VOLUME,
                UnitOfVolume.LITERS,
                None,
            ),
        ),
        (
            re.compile(r"resist", re.IGNORECASE),
            (
                None,
                "Ω",
                SensorStateClass.MEASUREMENT,
            ),
        ),
    ]

    @classmethod
    def infer_class(cls, key: str) -> SensorClassTuple | None:
        """Infer device class, unit and state class from key name."""
        key_lower = key.lower()

        for pattern, sensor_class in cls._CLASS_PATTERNS:
            if pattern.search(key_lower):
                return sensor_class

        return None


class SensorMetaHelper:
    """Helper class for sensor metadata such as units, descriptions, and icons."""

    @staticmethod
    def get_class(key: str) -> SensorClassTuple | None:
        """See UnitHelper.infer_unit()."""
        return SensorClassHelper.infer_class(key)

    @staticmethod
    def get_description(key: str) -> str:
        """Get description from key name using a dictionary mapping."""
        # Dictionary for key-to-description mapping
        description_mapping = {
            "sysLoadPwr": "Hausnetz",
            "housePower": "Hausnetz (berechnet)",
            "sysGridPwr": "Stromnetz",
            "gridPower": "Netzleistung",
            "gridToBattery": "Netz zu Batterie",
            "gridToHouse": "Netz zu Haus",
            "batteryToHouse": "Batterie zu Haus",
            "solarToBattery": "Solar zu Batterie",
            "solarToGrid": "Solar zu Netz",
            "solarToHouse": "Solar zu Haus",
            "mpptPwr": "Solarertrag",
            "bpPwr": "Batterieleistung",
            "bpSoc": "Ladezustand der Batterie",
            "online": "Online",
            "systemName": "System Name",
            "createTime": "Installations Datum",
            "bpVol": "Batteriespannung",
            "bpAmp": "Batteriestrom",
            "bpCycles": "Ladezyklen",
            "bpTemp": "Temperatur der Batteriezellen",
        }

        # Use .get() to avoid KeyErrors and return default value
        return description_mapping.get(key, key)  # Default to key if not found

    @staticmethod
    def get_special_icon(key: str) -> str | None:
        """Infer a Home Assistant icon from key semantics (generisch)."""
        k = key.lower()
        keyword_icons = [
            # Status / Diagnose
            (r"online$", "mdi:cloud-check"),
            (r"(code)", "mdi:alert-circle-outline"),
            (r"(ems|bms|bp).*state", "mdi:information-outline"),
            (r"(bmsrunsta|bmschgdsgsta)", "mdi:chip"),
            (r"(selfcheck|run)", "mdi:information-outline"),
            # Allgemeine Endungen
            (r"sn$", "mdi:barcode"),
            (r"name$", "mdi:label-outline"),
            (r"bright$", "mdi:brightness-percent"),
            # PV / MPPT
            (r"actpwr$", "mdi:flash"),
            (r"apparentpwr$", "mdi:flash-outline"),
            (r"reactpwr$", "mdi:sine-wave"),
            (r"electricitygeneration$", "mdi:counter"),
            (r"(pv|mppt).*lightsta", "mdi:white-balance-sunny"),
            (r"(pwrtotal|mpptpwr|pvinvpwr)$", "mdi:solar-power"),
            (r"(pv|mppt).*pwr", "mdi:solar-power-variant"),
            (r"(pv|mppt).*amp", "mdi:current-dc"),
            (r"(pv|mppt).*resist", "mdi:resistor"),
            (r"_pwr$", "mdi:flash"),
            # Netz / Haus / Batterie
            (r"solartobattery|solartogrid|solartohouse", "mdi:solar-power"),
            (
                r"(housepower|sysloadpwr|pcsactpwr|pcsmeterpower)",
                "mdi:home-lightning-bolt",
            ),
            (r"(gridtohouse|sysgridpwr|gridpower)", "mdi:transmission-tower-import"),
            (r"batterytohouse", "mdi:battery-arrow-up"),
            (r"gridtobattery", "mdi:battery-arrow-down"),
            # Strom / Spannung
            (r"_amp$", "mdi:current-ac"),
            # Batterie / Speicher
            (r"(soc)", "mdi:battery"),
            (r"(soh)", "mdi:battery-heart-variant"),
            (r"(remainwatth)", "mdi:home-battery"),
            (r"(temp|temperature)", "mdi:thermometer"),
            (r"cycles", "mdi:repeat"),
            (r"balancestate", "mdi:battery-sync"),
            (r"swver$", "mdi:cog"),
            (r"(bponlinesum|emsbpalivenum)$", "mdi:package-check"),
        ]

        for pattern, icon in keyword_icons:
            if re.search(pattern, k):
                return icon

        return None  # fallback
