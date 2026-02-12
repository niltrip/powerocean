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

from collections.abc import Callable
from enum import Enum
from typing import TypedDict

DEVICE_SN_KEYS = (
    "evSn",
    "hrSn",
    "bpSn",
    "devSn",
)


class ReportMode(Enum):
    """Enumeration for different report modes in the PowerOcean integration."""

    DEFAULT = "data"
    BATTERY = "BP_STA_REPORT"
    WALLBOX = "EDEV_PARAM_REPORT"
    WALLBOX_SYS = "EDEV_SYS_REPORT"
    HEATING_ROD = "HEATING_ROD_PARAM_REPORT"
    HEATING_ROD_ENERGY = "HEATING_ROD_ENERGY_STREAM_REPORT"
    CHARGEBOX = "EVCHARGING_REPORT"
    EMS = "EMS_HEARTBEAT"
    PARALLEL = "PARALLEL_ENERGY_STREAM_REPORT"
    EMS_CHANGE = "EMS_CHANGE_REPORT"
    ENERGY_STREAM = "ENERGY_STREAM_REPORT"


REPORT_DATAPOINTS: dict[str, set[str]] = {
    ReportMode.DEFAULT.value: {
        "sysLoadPwr",
        "sysGridPwr",
        "mpptPwr",
        "bpPwr",
        "online",
        "dcdcPwr",
        "todayElectricityGeneration",
        "monthElectricityGeneration",
        "yearElectricityGeneration",
        "totalElectricityGeneration",
        "systemName",
    },
    ReportMode.ENERGY_STREAM.value: {
        "pv1Pwr",
        "pvInvPwr",
        "pv2Pwr",
        "pv3Pwr",
    },
    ReportMode.EMS_CHANGE.value: {
        "bpTotalChgEnergy",
        "bpTotalDsgEnergy",
        "bpSoc",
        "bpOnlineSum",
        "emsCtrlLedBright",
        "mppt1FaultCode",
        "mppt1WarningCode",
        "mppt2FaultCode",
        "mppt2WarningCode",
        "mppt3FaultCode",
        "mppt3WarningCode",
    },
    ReportMode.BATTERY.value: {
        "bpSn",
        "bpPwr",
        "bpSoc",
        "bpSoh",
        "bpVol",
        "bpAmp",
        "bpCycles",
        "bpSysState",
        "bmsChgDsgSta",
        "bpBalanceState",
        "bpRemainWatth",
        "bmsRunSta",
        "bpEnvTemp",
        "bpMinCellTemp",
        "bpMaxCellTemp",
    },
    ReportMode.EMS.value: {
        "bpRemainWatth",
        "emsBpAliveNum",
        "emsBpPower",
        "emsBpSelfcheckState",
        "emsMpptSelfcheckState",
        "emsMpptRunState",
        "pcsActPwr",
        "pcsMeterPower",
    },
    ReportMode.CHARGEBOX.value: {
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
    },
    ReportMode.HEATING_ROD.value: {
        "hrSn",
        "selfcheckPercent",
        "temp",
        "targetTemp",
        # "onlineBits",
        "errorCode",
        "runFlag",
        "mode",
        "heatingPower",
        "waterTankVolume",
        "runStat",
        "targetPower",
    },
    ReportMode.HEATING_ROD_ENERGY.value: {
        "hrPwr",
        "fromPv",
        "fromBat",
        "fromGrid",
    },
    ReportMode.WALLBOX.value: {
        "devSn",
        "workMode",
        "chargeTarget",
        "switchBits",
        "chargeVehicleId",
        "chargingPwr",
        "timeToUseCar",
        "currentOuputMax",
        "userCurrentSet",
        "solarCurrentMin",
        "phaseSpecified",
        "chargingStatus",
    },
    ReportMode.WALLBOX_SYS.value: {
        "devSn",
        "allocatedPower",
        "realPowerLock",
        "refPower",
        "feedPwrCap",
        "startState",
        "errorCode",
        "warnCode",
        "socCur",
        "pclPwrBase",
    },
}


class BoxSchema(TypedDict):
    """Schema for a detected device (box) in PowerOcean responses."""

    detect: Callable[[dict], bool]
    mode: str  # "boxed" | "single"
    sn_path: list[str]
    model: str
    name_prefix: str
    paths: dict[str, list[str]] | None
    sensors: set[str]  # 🔑 jetzt als Set für schnelle Lookup


BOX_SCHEMAS: dict[str, BoxSchema] = {
    "battery": {
        "mode": "boxed",
        "detect": lambda p: "bpSn" in p,
        "sn_path": ["bpSn"],
        "model": "PowerOcean Battery",
        "name_prefix": "Battery",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.BATTERY.value],
    },
    "wallbox": {
        "mode": "boxed",
        "detect": lambda p: "pileChargingParamReport" in p,
        "sn_path": ["devInfo", "devSn"],
        "model": "PowerOcean PowerPulse",
        "name_prefix": "PowerPulse",
        "paths": {
            "devSn": ["devInfo", "devSn"],
            "workMode": ["pileChargingParamReport", "paramSet", "workMode"],
            "timeToUseCar": [
                "pileChargingParamReport",
                "paramSet",
                "smartMode",
                "timeToUseCar",
            ],
            "chargeTarget": [
                "pileChargingParamReport",
                "paramSet",
                "smartMode",
                "chargeTarget",
            ],
            "chargingStatus": ["pileChargingParamReport", "chargingStatus"],
            "chargingPwr": ["pileChargingParamReport", "chargingPwr"],
            "currentVehicleComsumption": ["vehicleInfo", "currentVehicleComsumption"],
            "orderChargingEnergy": [
                "orderRealStatus",
                "orderChargingEnergy",
            ],
        },
        "sensors": REPORT_DATAPOINTS[ReportMode.WALLBOX.value],
    },
    "charge": {
        "mode": "single",
        "detect": lambda p: "evPlugAndPlay" in p,
        "sn_path": ["evSn"],
        "model": "PowerOcean PowerPulse",
        "name_prefix": "PowerPulse",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.CHARGEBOX.value],
    },
    "heating_rod": {
        "mode": "single",
        "detect": lambda p: "hrSn" in p,
        "sn_path": ["hrSn"],
        "model": "PowerOcean PowerGlow",
        "name_prefix": "PowerGlow",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.HEATING_ROD.value],
    },
}


class DeviceRole(str, Enum):
    """Enumeration for device roles in the PowerOcean integration."""

    MASTER = "_master"
    SLAVE = "_slave"
    ALL = "_all"
    EMPTY = ""  # Used when no specific role is assigned


def _join_id(*parts: str) -> str:
    return "_".join(p for p in parts if p)
