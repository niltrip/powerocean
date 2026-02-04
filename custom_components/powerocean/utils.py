"""
Utility module for PowerOcean integration.

This module provides schema definitions, enumerations, and helper classes for
device detection, sensor metadata management, and reporting modes in the
PowerOcean Home Assistant integration.

Classes:
    BoxSchema: TypedDict for device schema definitions
    ReportMode: Enumeration for different report modes
    DeviceRole: Enumeration for device roles
    SensorMetaHelper: Helper class for sensor metadata (units, descriptions, icons)
"""

import logging
import re
from collections.abc import Callable
from enum import Enum
from typing import ClassVar, Optional, TypedDict


class BoxSchema(TypedDict):
    """
    Schema for a detected device (box) in PowerOcean responses.

    Attributes:
        detect: Callable that receives the response payload dict and returns True
            if the payload matches this schema.
        mode: "boxed" | "single" indicating how devices are grouped.
        sn_path: Path (list of keys) to the serial number in the payload.
        model: Human-readable model name.
        name_prefix: Prefix used for entity names.
        paths: Optional mapping of logical field names to paths in the payload.
        sensors: List of sensor keys expected for this schema.

    """

    detect: Callable[[dict], bool]
    mode: str  # "boxed" | "single"
    sn_path: list[str]
    model: str
    name_prefix: str
    paths: dict[str, list[str]] | None
    sensors: list[str]


BOX_SCHEMAS: dict[str, BoxSchema] = {
    "battery": {
        "mode": "boxed",  # 🔑 boxed | single
        "detect": lambda p: "bpSn" in p,
        "sn_path": ["bpSn"],
        "model": "PowerOcean Battery",
        "name_prefix": "Battery",
        "paths": None,
        "sensors": [
            "bpSn",
            "bpPwr",
            "bpSoc",
            "bpSoh",
            "bpVol",
            "bpAmp",
            "bpCycles",
            "bpSysState",
            "bpRemainWatth",
            "bmsRunSta",
            "bpEnvTemp",
            "bpMinCellTemp",
            "bpMaxCellTemp",
        ],
    },
    "wallbox": {
        "mode": "boxed",  # 🔑 boxed | single
        "detect": lambda p: "pileChargingParamReport" in p,
        "sn_path": ["devInfo", "devSn"],
        "model": "PowerOcean Wallbox",
        "name_prefix": "Wallbox",
        "paths": {
            "devSn": ["devInfo", "devSn"],
            "workMode": ["pileChargingParamReport", "paramSet", "workMode"],
            "userCurrentSet": ["pileChargingParamReport", "paramSet", "userCurrentSet"],
            "chargingPwr": ["pileChargingParamReport", "chargingPwr"],
        },
        "sensors": [
            "devSn",
            "workMode",
            "userCurrentSet",
            "chargingPwr",
        ],
    },
    "charge": {
        "mode": "single",  # 🔑 boxed | single
        "detect": lambda p: "evPlugAndPlay" in p,
        "sn_path": ["evSn"],
        "model": "PowerOcean Charger",
        "name_prefix": "Charger",
        "paths": None,
        "sensors": [
            "evSn",
            "workMode",
            "useGridFirst",
            "evOnoffSet",
            "orderStartTimestamp",
            "onlineBits",
            "errorCode",
            "evUserManual",
            "evChargingEnergy",
            "evCurrSet",
            "chargeVehicleId",
            "chargingStatus",
            "evPwr",
        ],
    },
}


class ReportMode(Enum):
    """Enumeration for different report modes in the PowerOcean integration."""

    DEFAULT = "data"
    BATTERY = "BP_STA_REPORT"
    WALLBOX = "EDEV_PARAM_REPORT"
    CHARGEBOX = "EVCHARGING_REPORT"
    EMS = "EMS_HEARTBEAT"
    PARALLEL = "PARALLEL_ENERGY_STREAM_REPORT"
    EMS_CHANGE = "EMS_CHANGE_REPORT"
    ENERGY_STREAM = "ENERGY_STREAM_REPORT"


class DeviceRole(str, Enum):
    """Enumeration for device roles in the PowerOcean integration."""

    MASTER = "_master"
    SLAVE = "_slave"
    ALL = "_all"
    EMPTY = ""  # Used when no specific role is assigned


class UnitHelper:
    """Helper class for unit inference from sensor key names using pattern matching."""

    _UNIT_PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = [
        (re.compile(r"(pwr|power)$"), "W"),
        (re.compile(r"(amp|current)$"), "A"),
        (re.compile(r"(vol|voltage)$"), "V"),
        (re.compile(r"(watth|energy)$"), "Wh"),
        (re.compile(r"(soc|soh|percent)$"), "%"),
        (re.compile(r"(temp|temperature)$"), "°C"),
        (re.compile(r"capacity"), "Ah"),
        (re.compile(r"generation"), "kWh"),
        (re.compile(r"volume"), "L"),
    ]

    @classmethod
    def infer_unit(cls, key: str) -> str | None:
        """
        Infer the physical unit from a sensor key name.

        The method matches common suffixes and keywords (e.g. "energy", "power",
        "voltage") against the given key name and returns the corresponding unit.

        Args:
            key: Sensor key name (e.g. "bpTotalChgEnergy").

        Returns:
            The inferred unit as a string (e.g. "Wh", "V", "%"),
            or None if no unit could be determined.

        """
        key_lower = key.lower()

        for pattern, unit in cls._UNIT_PATTERNS:
            if pattern.search(key_lower):
                return unit

        return None


class SensorMetaHelper:
    """Helper class for sensor metadata such as units, descriptions, and icons."""

    @staticmethod
    def get_unit(key: str) -> str | None:
        """See UnitHelper.infer_unit()."""
        return UnitHelper.infer_unit(key)

    @staticmethod
    def get_description(key: str) -> str:
        """Get description from key name using a dictionary mapping."""
        # Dictionary for key-to-description mapping
        description_mapping = {
            "sysLoadPwr": "Hausnetz",
            "sysGridPwr": "Stromnetz",
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
        """Get special icon for a key."""
        # Dictionary für die Zuordnung von Keys zu Icons
        icon_mapping = {
            "mpptPwr": "mdi:solar-power",
            "online": "mdi:cloud-check",
            "sysGridPwr": "mdi:transmission-tower-import",
            "sysLoadPwr": "mdi:home-import-outline",
            "bpAmp": "mdi:current-dc",
        }

        # Standardwert setzen
        special_icon = icon_mapping.get(key)

        # Zusätzliche Prüfung für Keys, die mit "pv1" oder "pv2" beginnen
        if key.startswith(("pv1", "pv2", "pv3")):
            special_icon = "mdi:solar-power"

        return special_icon
