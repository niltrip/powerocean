"""Constants for the PowerOcean integration."""

import logging

from homeassistant.const import Platform

DOMAIN = "powerocean"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL
LENGTH_BATTERIE_SN = 12  # Length of the battery serial number to identify battery data
USE_MOCKED_RESPONSE = False  # Set to True to use mocked responses for testing

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")

ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_DESTINATION_NAME = "Destination Name"
ATTR_SOURCE_NAME = "Source Name"
ATTR_UNIQUE_ID = "Internal Unique ID"
ATTR_PRODUCT_VENDOR = "Vendor"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"
ATTR_PRODUCT_NAME = "Device Name"
ATTR_PRODUCT_VERSION = "Vendor Firmware Version"
ATTR_PRODUCT_BUILD = "Vendor Product Build"
ATTR_PRODUCT_FEATURES = "Vendor Product Features"

# Standard-Metadaten pro Sensortyp
DEFAULT_META = {
    "power": {"unit": "W", "device_class": "power", "state_class": "measurement"},
    "energy": {
        "unit": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
    },
    "voltage": {"unit": "V", "device_class": "voltage", "state_class": "measurement"},
    "current": {"unit": "A", "device_class": "current", "state_class": "measurement"},
    "percent": {"unit": "%", "device_class": "battery", "state_class": "measurement"},
    "temperature": {
        "unit": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
    },
    "boolean": {"unit": None, "device_class": "running", "state_class": "measurement"},
    "count": {"unit": None, "device_class": None, "state_class": "measurement"},
}

# Kompakte Sensor-Mapping-Tabelle
SENSOR_META = {
    # Default Report: "data"
    "sysLoadPwr": DEFAULT_META["power"],
    "sysGridPwr": DEFAULT_META["power"],
    "mpptPwr": DEFAULT_META["power"],
    "bpPwr": DEFAULT_META["power"],
    "online": DEFAULT_META["boolean"],
    "dcdcPwr": DEFAULT_META["power"],
    "todayElectricityGeneration": DEFAULT_META["energy"],
    "monthElectricityGeneration": DEFAULT_META["energy"],
    "yearElectricityGeneration": DEFAULT_META["energy"],
    "totalElectricityGeneration": DEFAULT_META["energy"],
    "systemName": {"unit": None, "device_class": None, "state_class": None},
    # JTS1_ENERGY_STREAM_REPORT
    "pv1Pwr": DEFAULT_META["power"],
    "pvInvPwr": DEFAULT_META["power"],
    "pv2Pwr": DEFAULT_META["power"],
    "pv3Pwr": DEFAULT_META["power"],
    # JTS1_EMS_CHANGE_REPORT
    "bpTotalChgEnergy": DEFAULT_META["energy"],
    "bpTotalDsgEnergy": DEFAULT_META["energy"],
    "bpSoc": DEFAULT_META["percent"],
    "bpOnlineSum": DEFAULT_META["count"],
    "emsCtrlLedBright": DEFAULT_META["count"],
    "mppt1FaultCode": DEFAULT_META["count"],
    "mppt1WarningCode": DEFAULT_META["count"],
    "mppt2FaultCode": DEFAULT_META["count"],
    "mppt2WarningCode": DEFAULT_META["count"],
    # JTS1_BP_STA_REPORT
    "bpSoh": {**DEFAULT_META["percent"], "icon": "mdi:battery-heart"},
    "bpVol": DEFAULT_META["voltage"],
    "bpAmp": DEFAULT_META["current"],
    "bpCycles": DEFAULT_META["count"],
    "bpSysState": DEFAULT_META["count"],
    "bpRemainWatth": DEFAULT_META["energy"],
    "bmsRunSta": DEFAULT_META["count"],
    "bpEnvTemp": DEFAULT_META["temperature"],
    "bpMinCellTemp": DEFAULT_META["temperature"],
    "bpMaxCellTemp": DEFAULT_META["temperature"],
    # JTS1_EMS_HEARTBEAT
    "emsBpAliveNum": DEFAULT_META["count"],
    "emsBpPower": DEFAULT_META["power"],
    "pcsActPwr": DEFAULT_META["power"],
    "pcsMeterPower": DEFAULT_META["power"],
    # JTS1_EVCHARGING_REPORT
    "evSn": {"unit": None, "device_class": None, "state_class": None},
    "workMode": {"unit": None, "device_class": None, "state_class": None},
    "useGridFirst": DEFAULT_META["boolean"],
    "evOnoffSet": DEFAULT_META["boolean"],
    "orderStartTimestamp": {
        "unit": None,
        "device_class": "timestamp",
        "state_class": "measurement",
    },
    "onlineBits": DEFAULT_META["count"],
    "errorCode": DEFAULT_META["count"],
    "evUserManual": DEFAULT_META["boolean"],
    "evChargingEnergy": DEFAULT_META["energy"],
    "evCurrSet": DEFAULT_META["current"],
    "chargeVehicleId": {"unit": None, "device_class": None, "state_class": None},
    "chargingStatus": {"unit": None, "device_class": None, "state_class": None},
    "evPwr": DEFAULT_META["power"],
    # JTS1_HEATING_ROD_PARAM_REPORT
    "selfcheckPercent": DEFAULT_META["percent"],
    "temp": DEFAULT_META["temperature"],
    "targetTemp": DEFAULT_META["temperature"],
    "runFlag": DEFAULT_META["boolean"],
    "mode": {"unit": None, "device_class": None, "state_class": None},
    "heatingPower": DEFAULT_META["power"],
    "hrSn": {"unit": None, "device_class": None, "state_class": None},
    "waterTankVolume": {
        "unit": "L",
        "device_class": None,
        "state_class": "measurement",
    },
    "runStat": DEFAULT_META["count"],
    "targetPower": DEFAULT_META["power"],
}
