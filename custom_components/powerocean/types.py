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
from typing import TYPE_CHECKING, ClassVar, TypeAlias

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, PORTAL_URL
from .entity_names import NAMED
from .utils import (
    DIAGNOSTIC_KEYS,
    DIAGNOSTIC_PATTERNS,
    DISABLED_BY_DEFAULT,
    TRANSLATED_STATES,
    ReportMode,
    name_context,
    translation_key,
)

_DIAGNOSTIC_PATTERNS = [re.compile(pattern) for pattern in DIAGNOSTIC_PATTERNS]

# Decimal places Home Assistant shows, by unit; only the display changes
DISPLAY_PRECISION: dict[str, int] = {
    PERCENTAGE: 0,
    "Ω": 1,
}

if TYPE_CHECKING:
    from .devices import Device
    from .parse import Datapoint

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
        cls: (SensorClassTuple | None): Unit of measurement.
        description (str): Description of the endpoint.
        icon (str | None): Icon representing the endpoint.
        device_info (DeviceInfo): Inverter/Battery/Wallbox.

    """

    internal_unique_id: str
    serial: str
    name: str
    friendly_name: str
    cls: SensorClassTuple | None
    description: str
    icon: str | None
    device_info: DeviceInfo | None = None
    enabled_default: bool = True
    diagnostic: bool = False
    translation_key: str | None = None
    # The name comes from the translations (else the key is the name)
    named: bool = False
    translated_states: bool = False

    @classmethod
    def from_datapoint(cls, datapoint: "Datapoint") -> "PowerOceanEndPoint":
        """Return the endpoint of a parsed datapoint, with metadata from its key."""
        key = datapoint.key
        name_key = _name_translation_key(datapoint)
        states = key in TRANSLATED_STATES
        return cls(
            internal_unique_id=datapoint.unique_id,
            serial=datapoint.device.sn,
            name=f"{datapoint.device.sn}_{key}",
            friendly_name=key,
            cls=SensorMetaHelper.get_class(key),
            description=SensorMetaHelper.get_description(key),
            icon=SensorMetaHelper.get_special_icon(key),
            device_info=device_info(datapoint.device),
            enabled_default=datapoint.in_use and key not in DISABLED_BY_DEFAULT,
            diagnostic=_is_diagnostic(datapoint),
            translation_key=name_key or (translation_key(key) if states else None),
            named=name_key is not None,
            translated_states=states,
        )


def _name_translation_key(datapoint: "Datapoint") -> str | None:
    """Return the translation key of the datapoint's name, None without a name."""
    key = datapoint.key
    context = name_context(datapoint.unique_id)
    if context and (context, key) in NAMED:
        return f"{translation_key(key)}_{context}"
    if ("", key) in NAMED:
        return translation_key(key)
    return None


def _is_diagnostic(datapoint: "Datapoint") -> bool:
    """Return True if the entity belongs under "Diagnostic" despite its unit."""
    # The per-inverter energy streams of a dual system have no calculated
    # counterpart, so their values stay sensors
    if ReportMode.PARALLEL.value in datapoint.unique_id:
        return False
    key = datapoint.key
    return key in DIAGNOSTIC_KEYS or any(
        pattern.search(key) for pattern in _DIAGNOSTIC_PATTERNS
    )


def device_info(device: "Device") -> DeviceInfo:
    """Return the Home Assistant device info of a device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, device.sn)},
        serial_number=device.sn,
        name=device.name,
        manufacturer="EcoFlow",
        model=device.model,
        configuration_url=PORTAL_URL,
    )
    if device.via_sn:
        info["via_device"] = (DOMAIN, device.via_sn)
    return info


class SensorClassHelper:
    """Infer SensorDeviceClass, unit and SensorStateClass from a sensor key."""

    # Keys whose name ending does not tell the unit; checked first
    _KEY_CLASSES: ClassVar[dict[str, SensorClassTuple]] = {
        "pcsAcFreq": (
            SensorDeviceClass.FREQUENCY,
            UnitOfFrequency.HERTZ,
            SensorStateClass.MEASUREMENT,
        ),
        **dict.fromkeys(
            ("bpCellMaxVol", "bpCellMinVol"),
            (
                SensorDeviceClass.VOLTAGE,
                UnitOfElectricPotential.MILLIVOLT,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        **dict.fromkeys(
            ("pcsActivePowerLimitUp", "pcsActivePowerLimitDn"),
            (
                SensorDeviceClass.POWER,
                UnitOfPower.WATT,
                SensorStateClass.MEASUREMENT,
            ),
        ),
        **dict.fromkeys(
            (
                "bpTimestamp",
                # Wallbox: start of the charging order (ends in "amp", which
                # made it a current in A before)
                "orderStartTimestamp",
                "heartbeatUpdateTime",
                "energyStreamUpdateTime",
                "emsChangeUpdateTime",
            ),
            (SensorDeviceClass.TIMESTAMP, None, None),
        ),
        "emsNtcTempMax": (
            SensorDeviceClass.TEMPERATURE,
            UnitOfTemperature.CELSIUS,
            SensorStateClass.MEASUREMENT,
        ),
        **dict.fromkeys(
            (
                "sysBatChgUpLimit",
                "sysBatDsgDownLimit",
                "emsFeedRatio",
                "sysBatBackupRatio",
            ),
            (None, PERCENTAGE, SensorStateClass.MEASUREMENT),
        ),
        **dict.fromkeys(
            (
                "pcsOvpProtectCnt",
                "pcsAvgOvpProtectCnt",
                "pcsUvp1ProtectCnt",
                "pcsUvp2ProtectCnt",
                "pcsOfpProtectCnt",
                "pcsUfpProtectCnt",
            ),
            (None, None, SensorStateClass.TOTAL_INCREASING),
        ),
    }

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
        if key in cls._KEY_CLASSES:
            return cls._KEY_CLASSES[key]

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
            "batteryToGrid": "Batterie zu Netz",
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
            "errorCount": "Aktive Fehler",
            # Meaning not published by EcoFlow; observed: 0 by day, 2 at night
            **{
                f"mppt{n}FaultCode": (
                    f"Fehlercode PV-String {n} (Bedeutung nicht veröffentlicht; "
                    "nachts meist 2)"
                )
                for n in range(1, 5)
            },
        }

        # Use .get() to avoid KeyErrors and return default value
        return description_mapping.get(key, key)  # Default to key if not found

    # Icons of single keys; checked first. None: the icon of the device class,
    # e.g. the battery level icon that shows how full the battery is, or the
    # icon by state from icons.json.
    _KEY_ICONS: ClassVar[dict[str, str | None]] = {
        "bpSoc": None,
        "bpRealSoc": None,
        "emsWordMode": "mdi:cog-outline",
        # Icon by state from icons.json
        **dict.fromkeys(
            (
                "pcsRunSta",
                "dabModSta",
                "bmsChgDsgSta",
                "bmsRunSta",
                "online",
            ),
            None,
        ),
        "sysGridSta": "mdi:transmission-tower",
        "pcsAcFreq": "mdi:sine-wave",
        "sysBatChgUpLimit": "mdi:battery-arrow-up",
        "sysBatDsgDownLimit": "mdi:battery-arrow-down",
        "emsKeepSoc": "mdi:battery-lock",
        "sysBatBackupRatio": "mdi:battery-lock",
        **dict.fromkeys(
            ("pcsActivePowerLimitUp", "pcsActivePowerLimitDn"), "mdi:speedometer"
        ),
        **dict.fromkeys(
            ("batteryLimitReason", "pcsPowerDeratingFlag", "pcsPowerDeratingSet"),
            "mdi:speedometer-slow",
        ),
        **dict.fromkeys(
            ("emsFeedMode", "emsFeedPwr", "emsFeedRatio"),
            "mdi:transmission-tower-export",
        ),
        **dict.fromkeys(("breakerCapacityMax", "breakerEnableState"), "mdi:fuse"),
        **dict.fromkeys(
            ("sys14aEnable", "sys14aType", "relay14a"), "mdi:transmission-tower-off"
        ),
        **dict.fromkeys(("emsSgReadyEn", "emsSgRunStat"), "mdi:heat-pump"),
        "wifiStaStat": "mdi:wifi",
        "ethWanStat": "mdi:ethernet",
        "iot4gSta": "mdi:signal",
        **dict.fromkeys(
            (
                "pcsOvpProtectCnt",
                "pcsAvgOvpProtectCnt",
                "pcsUvp1ProtectCnt",
                "pcsUvp2ProtectCnt",
                "pcsOfpProtectCnt",
                "pcsUfpProtectCnt",
            ),
            "mdi:shield-alert-outline",
        ),
        **dict.fromkeys(
            ("meterL1Pwr", "meterL2Pwr", "meterL3Pwr", "meterTotalPwr"),
            "mdi:meter-electric",
        ),
    }

    @staticmethod
    def get_special_icon(key: str) -> str | None:
        """Infer a Home Assistant icon from key semantics (generisch)."""
        if key in SensorMetaHelper._KEY_ICONS:
            return SensorMetaHelper._KEY_ICONS[key]
        k = key.lower()
        keyword_icons = [
            # Status / Diagnose
            (r"online$", "mdi:cloud-check"),
            (r"errorcount$", "mdi:alert-circle-outline"),
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
            (r"batterytogrid", "mdi:transmission-tower-export"),
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
