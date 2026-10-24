"""
Utility module for PowerOcean integration.

This module provides schema definitions, enumerations, and helper classes for
device detection, sensor metadata management, and reporting modes in the
PowerOcean Home Assistant integration.

Classes:
    BoxSchema: TypedDict for device schema definitions
    ReportMode: Enumeration for different report modes
    SensorMetaHelper: Helper class for sensor metadata (units, descriptions, icons)
"""

import re
from collections.abc import Callable
from enum import Enum
from typing import TypedDict


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
    EMS_PARAM_CHANGE = "EMS_PARAM_CHANGE_REPORT"
    EMS_STATE_CHANGE = "EMS_STATE_CHANGE_REPORT"
    ENERGY_STREAM = "ENERGY_STREAM_REPORT"
    ERROR = "ERROR_CHANGE_REPORT"


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
        # Charge and discharge limit of the battery in %, as set in the app
        "sysBatChgUpLimit",
        "sysBatDsgDownLimit",
        "sysGridSta",
    },
    ReportMode.ENERGY_STREAM.value: {
        "pv1Pwr",
        "pv2Pwr",
        "pv3Pwr",
        "pvInvPwr",
        "otherPvPwr",
        "gridPwr",
        "loadPwr",
        "heatingPower",
        "dcdcPwr",
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
        # Operating state
        "emsWordMode",
        "pcsRunSta",
        "batteryLimitReason",
        "pcsPowerDeratingFlag",
        "pcsPowerDeratingSet",
        # Feed-in limit and backup reserve
        "emsFeedMode",
        "emsFeedPwr",
        "emsFeedRatio",
        "emsKeepSoc",
        "sysBatBackupRatio",
        # Controllable consumers (German §14a EnWG) and SG Ready heat pump
        "sys14aEnable",
        "sys14aType",
        "relay14a",
        "emsSgReadyEn",
        "emsSgRunStat",
        # Connection: Wi-Fi, Ethernet, 4G
        "wifiStaStat",
        "ethWanStat",
        "iot4gSta",
        # Error and warning codes of the inverter
        "pcsAcErrCode",
        "pcsDcErrCode",
        "pcsAcWarningCode",
        # How often the grid protection tripped (voltage, frequency)
        "pcsOvpProtectCnt",
        "pcsAvgOvpProtectCnt",
        "pcsUvp1ProtectCnt",
        "pcsUvp2ProtectCnt",
        "pcsOfpProtectCnt",
        "pcsUfpProtectCnt",
        # Load management of the house connection (PowerOcean Plus)
        "breakerCapacityMax",
        "breakerEnableState",
    },
    ReportMode.EMS_PARAM_CHANGE.value: {
        # Load management of the house connection
        "breakerCapacityMax",
        "breakerEnableState",
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
        "moduleAplSwVer",
        "bpAccuChgEnergy",
        "bpAccuDsgEnergy",
        # Highest and lowest cell voltage in mV
        "bpCellMaxVol",
        "bpCellMinVol",
        "bpRealSoh",
        "bpRealSoc",
        "bpMaxMosTemp",
        "bpMinMosTemp",
        "dabModSta",
        "bpErrCode",
        # Time of the battery's last values (Unix time)
        "bpTimestamp",
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
        "emsSystemState",
        "mpptRatePower",
        "gridOutDayEnergy",
        "meterACurrent",
        "meterBCurrent",
        "bpInDayEnergy",
        "meterPowerFactor",
        "isPvToInvDirectly",
        "bp1SocCoefficient",
        "sysRunMeterFeedPower",
        "inverterBand",
        "loadDayEnergy",
        "startDischargePower",
        "bpDichargeAbilityAdjustValueLv2",
        "bpDichargeAbilityAdjustValueLv1",
        "meterAddress",
        "gridFeedRate",
        "currentNetif",
        "batterySocLowerLimit",
        "bpChgDsgSta",
        "autoDetectStartPowerEn",
        "meterPhasePower",
        "sysErrCode",
        "meterTotalPower",
        "bpBusVoltageBaseCoefficient",
        "bpSaveSocStopDelay",
        "bpOutDayEnergy",
        "bpBusVoltCoefficient",
        "pvInvWiringMode",
        "underVoltageProtectPoint",
        "batterySocUpperLimit",
        "bpRunDelay",
        "meterBVoltage",
        "bpStopDelay",
        "errCode",
        "gridInDayEnergy",
        "sysRunMeterTakePower",
        "bpSoc",
        "bp3SocCoefficient",
        "bpErrorCodeMask",
        "workingMode",
        "appCtrlState",
        "bpSocBaseCoefficient",
        "emsErrorCodeMask",
        "meterRatio",
        "sysWorkSta",
        "pv2ErrorCodeMask",
        "pv1ErrorCodeMask",
        "manualSetStartChargePower",
        "pvWiringType",
        "emsErrCode",
        "bp2SocCoefficient",
        "mpptWithstandVoltage",
        "meterCVoltage",
        "pvaErrorCodeMask",
        "meterAVoltage",
        "invWiringType",
        "disOrChargeAbilityStepValue",
        "reportCycleTime",
        "pvInDayEnergy",
        "meterType",
        "meterCCurrent",
        "mpptVoltageMinimum",
        "mpptVoltageMaximum",
        "gridFeedPowerMinimum",
        "epoSwitchState",
        "pcsAcFreq",
        # Smart meter (meterHeartBeat, meter type 11): power per phase and
        # total in W, positive = from the grid
        "meterL1Pwr",
        "meterL2Pwr",
        "meterL3Pwr",
        "meterTotalPwr",
        # Current power limit of the inverter (charge / discharge)
        "pcsActivePowerLimitUp",
        "pcsActivePowerLimitDn",
        "emsNtcTempMax",
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
    # Calculated from the error codes of all modules, see errors.py
    ReportMode.ERROR.value: {
        "errorCount",
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


# Time of the last report, as a datapoint of the inverter. Shows how old
# its values are; the key names the report, the value is its updateTime.
UPDATE_TIME_KEYS: dict[str, str] = {
    ReportMode.EMS.value: "heartbeatUpdateTime",
    ReportMode.ENERGY_STREAM.value: "energyStreamUpdateTime",
    # Sent on changes only, so it can be hours old (battery energy totals)
    ReportMode.EMS_CHANGE.value: "emsChangeUpdateTime",
}

# Entities of these keys are shown under "Diagnostic", although they have
# a unit (entities without a unit always are)
DIAGNOSTIC_KEYS: set[str] = {
    # State of health of the battery packs
    "bpSoh",
    "bpRealSoh",
    # Cell voltages of the battery packs
    "bpCellMaxVol",
    "bpCellMinVol",
    # Grid frequency and inverter temperature
    "pcsAcFreq",
    "emsNtcTempMax",
    # Limits and settings
    "pcsActivePowerLimitUp",
    "pcsActivePowerLimitDn",
    "emsFeedPwr",
    "emsFeedRatio",
    "emsKeepSoc",
    "sysBatBackupRatio",
    "sysBatChgUpLimit",
    "sysBatDsgDownLimit",
    # Values of the API that are also calculated from the heartbeat, which
    # updates more often: see housePower, gridPower and mpptPv_pwrTotal
    "sysLoadPwr",
    "sysGridPwr",
    "mpptPwr",
    # Details of the inverter: shown elsewhere as overview or energy flow
    "mpptInsResist",
    "pcsActPwr",
    "pcsMeterPower",
    "emsBpPower",
    "pv1Pwr",
    "pv2Pwr",
    "pv3Pwr",
    "pvInvPwr",
    "otherPvPwr",
    # Details of the battery packs
    "bpVol",
    "bpAmp",
    "bpEnvTemp",
    "bpMaxCellTemp",
    "bpMinCellTemp",
}

# ... and the keys matching these patterns (regular expressions)
DIAGNOSTIC_PATTERNS: tuple[str, ...] = (
    # Voltage, current and power of each phase
    r"^pcs[ABC]Phase_",
    # Voltage and current of each PV string (their power stays)
    r"^mpptPv\d+_(vol|amp)$",
)

# Entities of these keys show translated states (translations/*.json,
# entity.sensor.<key in snake_case>.state). Their state is the value in
# lowercase, as Home Assistant requires for translated states; values
# without a translation are shown as they are.
TRANSLATED_STATES: set[str] = {
    "emsWordMode",
    "pcsRunSta",
    "dabModSta",
    "sys14aEnable",
    "emsSgReadyEn",
    "breakerEnableState",
    # Battery packs (their state was the raw value before, e.g. PB_CHG_STATE)
    "bmsChgDsgSta",
    "bmsRunSta",
    "bpSysState",
    # Online/Offline (state stays "1"/"0")
    "online",
}

# Context of a datapoint whose name depends on where it appears: the values
# of a battery pack, or of one inverter in a dual system (see
# scripts/entity_names.csv, column "context")
NAME_CONTEXTS: dict[str, str] = {
    ReportMode.BATTERY.value: "pack",
    ReportMode.PARALLEL.value: "inverter",
}


def name_context(unique_id: str) -> str:
    """Return the name context of a datapoint, "" if none applies."""
    for report, context in NAME_CONTEXTS.items():
        if report in unique_id:
            return context
    return ""


def translation_key(key: str) -> str:
    """Return the translation key of a datapoint key: emsWordMode -> ems_word_mode."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    # mpptPv1_vol -> mppt_pv1_vol (hassfest rejects "__")
    return re.sub(r"_+", "_", snake)


# Entities of these keys are created disabled; users can enable them.
# Applies to new entities only: existing ones keep their state.
DISABLED_BY_DEFAULT: set[str] = {
    # Protection counters, so far always 0
    "pcsAvgOvpProtectCnt",
    "pcsOfpProtectCnt",
    "pcsOvpProtectCnt",
    "pcsUfpProtectCnt",
    "pcsUvp1ProtectCnt",
    "pcsUvp2ProtectCnt",
    # Error and warning codes: active errors are shown by errorCount
    "pcsAcErrCode",
    "pcsDcErrCode",
    "pcsAcWarningCode",
    *(f"mppt{n}FaultCode" for n in range(1, 5)),
    *(f"mppt{n}WarningCode" for n in range(1, 5)),
    # States without known meaning or always the same
    "emsBpSelfcheckState",
    "emsMpptSelfcheckState",
    "emsMpptRunState",
    *(f"mpptPv{n}_lightSta" for n in range(1, 5)),
    "sysGridSta",
    "batteryLimitReason",
    "pcsPowerDeratingFlag",
    # Settings that hardly ever change
    "breakerCapacityMax",
    "breakerEnableState",
    "pcsActivePowerLimitUp",
    "pcsActivePowerLimitDn",
    "emsFeedMode",
    "emsCtrlLedBright",
    # Details: reactive and apparent power of each phase
    *(f"pcs{phase}Phase_reactPwr" for phase in "ABC"),
    *(f"pcs{phase}Phase_apparentPwr" for phase in "ABC"),
    # Same as bpOnlineSum
    "emsBpAliveNum",
    "systemName",
    # One time of the last values is enough: heartbeatUpdateTime
    "emsChangeUpdateTime",
    "energyStreamUpdateTime",
    # Connection codes without known meaning
    "wifiStaStat",
    "ethWanStat",
    "iot4gSta",
    # Only of interest with §14a EnWG or an SG Ready heat pump
    "sys14aEnable",
    "emsSgReadyEn",
    "sys14aType",
    "relay14a",
    "emsSgRunStat",
    "pcsPowerDeratingSet",
    # Second PV inverter: no hint in the response whether there is one
    "pvInvPwr",
    "otherPvPwr",
    # Settings that hardly ever change
    "emsFeedRatio",
    "emsKeepSoc",
    "sysBatBackupRatio",
    # Internal battery values
    "bpMaxMosTemp",
    "bpMinMosTemp",
    "bpRealSoc",
}


class BoxSchema(TypedDict):
    """Schema for a detected device (box) in PowerOcean responses."""

    detect: Callable[[dict], bool]
    sn_path: list[str]
    model: str
    name_prefix: str
    paths: dict[str, list[str]] | None
    sensors: set[str]  # 🔑 jetzt als Set für schnelle Lookup


BOX_SCHEMAS: dict[str, BoxSchema] = {
    "battery": {
        "detect": lambda p: "bpSn" in p,
        "sn_path": ["bpSn"],
        "model": "PowerOcean Battery",
        "name_prefix": "Battery",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.BATTERY.value],
    },
    "wallbox": {
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
        "detect": lambda p: "evPlugAndPlay" in p,
        "sn_path": ["evSn"],
        "model": "PowerOcean PowerPulse",
        "name_prefix": "PowerPulse",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.CHARGEBOX.value],
    },
    "heating_rod": {
        "detect": lambda p: "hrSn" in p,
        "sn_path": ["hrSn"],
        "model": "PowerOcean PowerGlow",
        "name_prefix": "PowerGlow",
        "paths": None,
        "sensors": REPORT_DATAPOINTS[ReportMode.HEATING_ROD.value],
    },
}


def _join_id(*parts: str) -> str:
    return "_".join(p for p in parts if p)


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


def decode_version(value: int) -> str:
    """
    Decode a 32-bit integer into a dotted version string.

    The integer is interpreted as:
        - bits 24-31: major
        - bits 16-23: minor
        - bits 8-15: patch
        - bits 0-7: build

    Args:
        value: Encoded 32-bit version integer.

    Returns:
        Version string in the format "major.minor.patch.build".

    """
    major = (value >> 24) & 0xFF
    minor = (value >> 16) & 0xFF
    patch = (value >> 8) & 0xFF
    build = value & 0xFF
    return f"{major}.{minor}.{patch}.{build}"
