"""ecoflow.py: API for PowerOcean integration."""

import asyncio
import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, ClassVar, TypeAlias

import aiohttp
import orjson
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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util.json import json_loads
from pydantic import Json

from .const import (
    DOMAIN,
    ISSUE_URL_ERROR_MESSAGE,
    LOGGER,
    MOCKED_RESPONSE,
    MODEL_NAME_MAP,
    USE_MOCKED_RESPONSE,
    PowerOceanModel,
)
from .utils import (
    BOX_SCHEMAS,
    REPORT_DATAPOINTS,
    BoxSchema,
    DeviceRole,
    ReportMode,
    _join_id,
)

SensorClassTuple: TypeAlias = tuple[
    SensorDeviceClass | None,
    str | None,
    SensorStateClass | None,
]


# Better storage of PowerOcean endpoint
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
            re.compile(r"(pwr|power|pwrTotal|grid|bat|pv)$", re.IGNORECASE),
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
            re.compile(r"(watth)$", re.IGNORECASE),
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
        """Infer a Home Assistant icon from key semantics (generisch)."""
        k = key.lower()
        keyword_icons = [
            # Status / Diagnose
            (r"online$", "mdi:cloud-check"),
            (r"(code)", "mdi:alert-circle-outline"),
            (r"(ems|bms|bp).*state", "mdi:information-outline"),
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
            (r"(pwrtotal|mpptpwr|pvInvPwr)$", "mdi:solar-power"),
            (r"(pv|mppt).*pwr", "mdi:solar-power-variant"),
            (r"(pv|mppt).*amp", "mdi:current-dc"),
            (r"(pv|mppt).*resist", "mdi:resistor"),
            (r"_pwr$", "mdi:flash"),
            (r"sysgridpwr$", "mdi:transmission-tower-import"),
            (r"(sysloadpwr|pcsmeterpower|pcsActPwr)$", "mdi:home-lightning-bolt"),
            # Strom / Spannung
            (r"_amp$", "mdi:current-ac"),
            # Batterie / Speicher
            (r"(soc|soh)", "mdi:battery"),
            (r"(remainwatth)", "mdi:car-battery"),
            (r"(temp|temperature)", "mdi:thermometer"),
            (r"cycles", "mdi:repeat"),
            (r"balancestate", "mdi:battery-sync"),
            (r"(bpOnlineSum|emsbpalivenum)$", "mdi:package-check"),
        ]

        for pattern, icon in keyword_icons:
            if re.search(pattern, k):
                return icon

        return None  # fallback


# ecoflow_api to detect device and get device info,
# fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june


class EcoflowApi:
    """Class representing Ecoflow."""

    def __init__(
        self,
        hass: HomeAssistant,
        serialnumber: str,
        username: str,
        password: str,
        variant: str,
    ) -> None:
        """
        Initialize the EcoFlow API client.

        Args:
            hass: Home Assistant instance.
            serialnumber: Serial number of the PowerOcean device.
            username: EcoFlow account username (email).
            password: EcoFlow account password.
            variant: PowerOcean device variant / model ID.

        """
        self.sn = serialnumber
        self.sn_inverter = ""
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.ecoflow_variant = variant
        self.token = None
        self.device = None
        self.url_authorize = "https://api.ecoflow.com/auth/login"
        self.url_fetch_data = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        self.hass = hass

    async def async_authorize(self) -> bool:
        """Authorize user and retrieve authentication token."""
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.ecoflow_username,
            "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        url = self.url_authorize
        LOGGER.info("Attempting to log in to EcoFlow API: %s", url)

        # Nutze die von HA bereitgestellte ClientSession
        session = async_get_clientsession(self.hass)

        try:
            # Asynchroner Request mit Timeout-Management
            async with asyncio.timeout(10):
                response = await session.post(url, json=data, headers=headers)
                response.raise_for_status()  # Wirft Exception bei 4xx/5xx Fehlern
                response_data = await response.json()

            LOGGER.debug("API Response: %s", response_data)
            data_block = response_data.get("data")

            if not isinstance(data_block, dict) or "token" not in data_block:
                msg = "Missing or malformed 'data' block in response"
                LOGGER.error(msg)
                raise AuthenticationFailedError(msg)

            self.token = data_block["token"]

        except (TimeoutError, aiohttp.ClientError):
            error_msg = f"Unable to connect to {url}. Device might be offline."
            LOGGER.exception("%s %s", error_msg, ISSUE_URL_ERROR_MESSAGE)

        except AuthenticationFailedError:
            LOGGER.exception("Unexpected error during login")
            msg = "Login failed due to unexpected error"
            raise IntegrationError(msg)

        LOGGER.info("Successfully logged in.")
        # Auch get_device() muss nun awaitable (async) sein
        # await self.async_get_device()
        return True

    async def fetch_raw(self) -> Json:
        """Fetch data from Url (Async version)."""
        url = self.url_fetch_data
        headers = {
            "authorization": f"Bearer {self.token}",
            "user-agent": "Firefox/133.0",
            "product-type": self.ecoflow_variant,
        }

        try:
            # 1. API-Abfrage über die zentrale HA-Session
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(30):
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    api_response = await response.json()

            # Mocked Response überschreibt echte API-Antwort
            if USE_MOCKED_RESPONSE and MOCKED_RESPONSE.exists():

                def load_mock_file() -> Json:
                    return json_loads(MOCKED_RESPONSE.read_text(encoding="utf-8"))

                api_response = await self.hass.async_add_executor_job(load_mock_file)

            return self._validate_response(api_response)

        except (TimeoutError, aiohttp.ClientError) as err:
            error_msg = f"ConnectionError in fetch_raw: Unable to connect to {url}."
            LOGGER.warning("%s %s", error_msg, ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error_msg) from err

    def _validate_response(self, response: Json) -> dict:
        """
        Validate EcoFlow API response structure.

        Ensures the response matches the expected EcoFlow API contract.

        Raises:
            IntegrationError: If the response is invalid or malformed.

        """
        if not isinstance(response, dict):
            msg = "API response is not a JSON object"
            raise IntegrationError(msg)

        data = response.get("data")
        if data is None:
            msg = "API response missing required 'data' field"
            raise IntegrationError(msg)

        if not isinstance(data, dict):
            msg = "API response field 'data' is not an object"
            raise IntegrationError(msg)

        return response

    def parse_structure(self, response: dict) -> dict[str, PowerOceanEndPoint]:
        """
        Parse the API response and return the device structure.

        Extracts all devices, endpoints, and their relationships from the raw
        EcoFlow API response. This is used to build entities and device registry
        entries.
        """
        collector = StructureCollector()
        self._walk_reports(response, collector)
        return collector.endpoints

    def parse_values(self, response: dict) -> dict[str, float | int | str]:
        """
        Parse the API response and return current sensor values.

        Extracts only live values from the EcoFlow API response, without
        any structural or device metadata.
        """
        collector = ValueCollector()
        self._walk_reports(response, collector)
        return collector.values

    def get_device(self) -> dict:
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "EcoFlow",
            "serial": self.sn,
            "name": f"PowerOcean {self.sn}",
            "model": MODEL_NAME_MAP[PowerOceanModel(self.ecoflow_variant)],
            "features": "Photovoltaik",
        }

        return self.device

    def _walk_reports(self, response: dict, collector) -> None:
        # error handling for response
        data = response.get("data")
        if not isinstance(data, dict):
            return

        self.sn_inverter = self.sn
        reports_data = list(REPORT_DATAPOINTS.keys())
        reports = []

        # Handle generic 'data' report
        if ReportMode.DEFAULT.value in reports_data:
            self._extract_sensors_from_report(
                response,
                report=ReportMode.DEFAULT.value,
                collector=collector,
            )
            reports = [r for r in reports_data if r != ReportMode.DEFAULT.value]
            LOGGER.debug(f"Reports to look for: {reports}")

        response_parallel = data.get("parallel")
        response_quota = data.get("quota")

        # Dual inverter installation
        if response_parallel:
            inverters = list(response_parallel.keys()) or [self.sn]

            for element in inverters:
                self.sn_inverter = element
                response_base = response_parallel.get(element, {})
                suffix = (
                    DeviceRole.MASTER.value
                    if element == self.sn
                    else DeviceRole.SLAVE.value
                )
                for report in reports:
                    report_key = (
                        ReportMode.PARALLEL.value
                        if ReportMode.ENERGY_STREAM.value in report
                        else report
                    )

                    self._extract_sensors_from_report(
                        response_base,
                        report_key,
                        suffix=suffix,
                        parallel_energy_stream_mode=ReportMode.PARALLEL.value
                        in report_key,
                        collector=collector,
                    )
        # Single inverter installation
        elif response_quota:
            response_base = response_quota
            for report in reports:
                self._extract_sensors_from_report(
                    response_base,
                    report,
                    collector=collector,
                )
        else:
            LOGGER.warning(
                "Neither 'quota' nor 'parallel' inverter data found in response."
            )
        return

    def _get_device_info(
        self,
        sn: str,
        name: str,
        model: str,
        via_sn: str | None = None,
    ) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            serial_number=sn,
            name=name,
            manufacturer="EcoFlow",
            model=model,
        )
        if via_sn:
            info["via_device"] = (DOMAIN, via_sn)
        return info

    def _parse_battery_data(self, raw_data: dict | str | None) -> dict | None:
        if raw_data is None:
            LOGGER.debug("Battery payload is None (no battery present)")
            return None

        if isinstance(raw_data, dict):
            return raw_data

        if isinstance(raw_data, str):
            try:
                data = json_loads(raw_data)
            except orjson.JSONDecodeError as err:
                LOGGER.warning("Failed to decode battery JSON: %s", err)
                return None

            if isinstance(data, dict):
                return data

            LOGGER.debug(
                "Battery JSON decoded but is %s instead of dict",
                type(data).__name__,
            )
            return None

        LOGGER.debug(
            "Unexpected battery payload type: %s",
            type(raw_data).__name__,
        )
        return None

    def _deep_get_by_key(self, data: Any, target_key: str) -> None:
        """Search recursively for the first occurrence of target_key in nested dict/list."""
        if isinstance(data, dict):
            for key, value in data.items():
                # Treffer
                if key == target_key:
                    return value

                # Rekursiv tiefer suchen
                result = self._deep_get_by_key(value, target_key)
                if result is not None:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = self._deep_get_by_key(item, target_key)
                if result is not None:
                    return result
        return None

    @staticmethod
    def _get_nested_value(data: dict[str, Any], path: list[str]) -> Any | None:
        for key in path:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
        return data

    def _detect_box_schema(self, payload: dict) -> tuple[str, BoxSchema] | None:
        for box_type, schema in BOX_SCHEMAS.items():
            detect_fn = schema.get("detect")
            if not callable(detect_fn):
                continue  # Kein detect-Feld, überspringen
            try:
                if detect_fn(payload):
                    return box_type, schema
            except (KeyError, TypeError, AttributeError) as e:
                # Loggen statt blind zu ignorieren
                LOGGER.warning("Error detecting box schema for %s: %s", box_type, e)
                continue
        return None

    def _extract_box_sn(
        self, payload: dict[str, Any], schema: BoxSchema, fallback_sn: str
    ) -> str | None:
        path = schema.get("sn_path")

        sn_value = self._get_nested_value(payload, path) if path else fallback_sn

        # nur strings weitergeben
        sn = sn_value if isinstance(sn_value, str) else None
        if not sn:
            return None

        return self._decode_sn(sn)

    def _extract_box_value(
        self,
        payload: dict,
        key: str,
        schema: BoxSchema,
    ) -> None:
        paths = schema.get("paths")
        value = (
            self._get_nested_value(payload, paths[key])
            if paths and key in paths
            else payload.get(key)
        )

        if value is None:
            return None

        if key.endswith("Sn") and isinstance(value, str):
            return self._decode_sn(value)

        return value

    def _make_box_device_info(
        self,
        sn: str,
        schema: BoxSchema,
    ) -> DeviceInfo:
        return self._make_device_info(
            sn=sn,
            prefix=schema["name_prefix"],
            model=schema["model"],
            via_sn=self.sn_inverter,
        )

    @staticmethod
    def _is_matching_report(key: str, report: str) -> bool:
        if not isinstance(key, str):
            return False

        # Spezialfall ENERGY_STREAM_REPORT
        if report == ReportMode.ENERGY_STREAM.value:
            return key.split("_", 1)[1] == report

        # Default-Fall
        return key.endswith(report)

    def _decode_sn(self, value: str | None) -> str | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return base64.b64decode(value, validate=True).decode("utf-8").strip()
        except binascii.Error:
            LOGGER.warning("Invalid base64 string for SN: %s", value)
            return value
        except UnicodeDecodeError:
            return value

    def _resolve_device_info(
        self,
        payload: dict,
        report: str,
    ) -> tuple[str, DeviceInfo]:
        """Resolve serial number and DeviceInfo for non-boxed reports."""
        # bekannte SN-Felder (Reihenfolge = Priorität)
        SN_KEYS = ("evSn", "hrSn")

        device_sn = self.sn_inverter
        prefix = "PowerOcean"
        model = MODEL_NAME_MAP[PowerOceanModel(self.ecoflow_variant)]
        via_sn = self.sn_inverter

        for key in SN_KEYS:
            raw_sn = payload.get(key)
            if isinstance(raw_sn, str):
                decoded = self._decode_sn(raw_sn)
                if decoded:
                    device_sn = decoded
                    prefix = "Charger" if key == "evSn" else "Heating Rod"
                    model = f"PowerOcean {prefix}"
                    via_sn = self.sn_inverter
                    break

        device_info = self._make_device_info(
            sn=device_sn,
            prefix=prefix,
            model=model,
            via_sn=via_sn,
        )

        return device_sn, device_info

    def _make_device_info(
        self, sn: str, prefix: str, model: str, via_sn: str | None = None
    ) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            serial_number=sn,
            name=f"{prefix} {sn}",
            manufacturer="EcoFlow",
            model=model,
        )
        if via_sn:
            info["via_device"] = (DOMAIN, via_sn)
        return info

    def _collect_sensor(
        self, collector, device_sn, report, key, value, device_info=None, suffix=""
    ) -> None:
        unique_id = _join_id(device_sn, report, key)
        collector.add(
            unique_id=unique_id,
            device_sn=device_sn,
            key=key,
            value=value,
            device_info=device_info,
            name=f"{device_sn}_{key}{suffix}",
            friendly_name=f"{key}{suffix}",
        )

    def _extract_sensors_from_report(
        self,
        response: dict[str, Any],
        report: str,
        suffix: str = "",
        *,
        parallel_energy_stream_mode: bool = False,
        collector,
    ) -> None:
        """
        Allgemeine Methode zum Extrahieren von Sensoren aus einem Report.

        Args:
            response: API Antwort als dict
            sensors: bisher gesammelte Sensoren (wird erweitert)
            report: Name des Reports im JSON
            suffix: Suffix, das an die Namen der Sensoren angehängt wird
                (z.B. für Master/Slave).
            parallel_energy_stream_mode: Wenn True, wird die spezielle Verarbeitung
                für parallele Energie-Streams verwendet.

        Returns:
            Erweitertes sensors dict mit neuen Sensoren

        """
        # Report-Key ggf. anpassen
        report_to_log = report

        key, d = next(
            (
                (k, v)
                for k, v in response.items()
                if self._is_matching_report(k, report)
            ),
            (None, None),
        )
        d = response.get(key) if key else None

        if not isinstance(d, dict):
            LOGGER.debug("Configured report '%s' not in response.", report_to_log)
            return

        sens_select = list(REPORT_DATAPOINTS.get(report, ()))
        # Setze Report-Namen korrekt aus response
        report = key or report
        # Battery und Wallbox Handling
        if ReportMode.BATTERY.value in report or ReportMode.WALLBOX.value in report:
            self._handle_boxed_devices(
                d,
                report=report,
                collector=collector,
            )
            return
        # EMS Heartbeat Mode
        if ReportMode.EMS.value in report:
            self._handle_ems_heartbeat_mode(
                d,
                report,
                sens_select,
                suffix,
                collector=collector,
            )
            return

        # Heating Rod Energy Stream Mode
        if ReportMode.HEATING_ROD_ENERGY.value in report:
            self._handle_heating_rod_energy_stream(
                d,
                report,
                sens_select=sens_select,
                collector=collector,
            )
            return

        if ReportMode.WALLBOX_SYS.value in report:
            self._handle_edev_device(
                d,
                report=report,
                sens_select=sens_select,
                collector=collector,
            )
            return

        # Parallel Energy Stream Mode
        if parallel_energy_stream_mode:
            self._handle_parallel_energy_stream(
                d,
                report,
                collector,
            )
        # Standardverarbeitung
        self._handle_standard_mode(
            d, report, sens_select, suffix=suffix, collector=collector
        )

    def _handle_boxed_devices(
        self,
        d: dict,
        *,
        report: str,
        collector,
    ):
        for box_sn_raw, raw_payload in d.items():
            if box_sn_raw in ("", "updateTime"):
                continue

            payload = self._parse_battery_data(raw_payload)
            if not isinstance(payload, dict):
                continue

            detected = self._detect_box_schema(payload)

            if not detected:
                LOGGER.debug("Unknown boxed device schema")
                continue

            # box_type wird aktuell nicht genutzt
            _, schema = detected

            device_sn = self._extract_box_sn(payload, schema, box_sn_raw)
            if not device_sn:
                continue

            device_info = self._make_box_device_info(device_sn, schema)

            for key in schema["sensors"]:
                value = self._extract_box_value(payload, key, schema)
                if value is None:
                    continue

                self._collect_sensor(
                    collector=collector,
                    device_sn=device_sn,
                    report=report,
                    key=key,
                    value=value,
                    device_info=device_info,
                )

    def _handle_ems_heartbeat_mode(
        self,
        d: dict,
        report: str,
        sens_select: list,
        suffix: str = "",
        *,
        collector,
    ):
        device_info = self._make_device_info(
            sn=self.sn_inverter,
            prefix="PowerOcean",
            model=MODEL_NAME_MAP[PowerOceanModel(self.ecoflow_variant)],
            via_sn=self.sn_inverter,
        )

        # EMS Heartbeat: ggf. verschachtelte Strukturen, spezielle Behandlung
        for key, value in d.items():
            if key in sens_select:
                self._collect_sensor(
                    collector,
                    self.sn_inverter,
                    report,
                    key,
                    value,
                    device_info=device_info,
                    suffix="",
                )
        # Besonderheiten Phasen
        phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
        if all(phase in d for phase in phases):
            for _, phase in enumerate(phases):
                for key, value in d[phase].items():
                    key_ext = f"{phase}_{key}"
                    self._collect_sensor(
                        collector,
                        self.sn_inverter,
                        report,
                        key_ext,
                        value,
                        device_info=device_info,
                        suffix="",
                    )

        # Besonderheit mpptPv
        if "mpptHeartBeat" in d:
            n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])
            report = f"{report}_mpptHeartBeat"
            for i in range(n_strings):
                for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                    key_ext = f"mpptPv{i + 1}_{key}"
                    self._collect_sensor(
                        collector,
                        self.sn_inverter,
                        report,
                        key_ext,
                        value,
                        device_info=device_info,
                        suffix="",
                    )

            # Gesamtleistung mpptPv
            total_power = sum(
                d["mpptHeartBeat"][0]["mpptPv"][i].get("pwr", 0)
                for i in range(n_strings)
            )
            key_ext = "mpptPv_pwrTotal"
            self._collect_sensor(
                collector,
                self.sn_inverter,
                report,
                key_ext,
                total_power,
                device_info=device_info,
                suffix="",
            )

            # --- Isolationswiderstand ---
            mppt_ins_resist = d["mpptHeartBeat"][0].get("mpptInsResist")
            if mppt_ins_resist is not None:
                self._collect_sensor(
                    collector,
                    self.sn_inverter,
                    report,
                    "mpptInsResist",
                    mppt_ins_resist,
                    device_info=device_info,
                    suffix="",
                )

    def _handle_heating_rod_energy_stream(
        self,
        d: dict,
        report: str,
        sens_select: list,
        collector,
    ):
        """Handle heating rod energy stream extraction."""

        stream_list = d.get("hrEnergyStream")
        if not isinstance(stream_list, list):
            return

        for element in stream_list:
            if not isinstance(element, dict):
                continue

            raw_sn = element.get("hrSn")
            device_sn = self._decode_sn(raw_sn) if raw_sn else None
            if not device_sn:
                continue

            device_info = self._make_device_info(
                sn=device_sn,
                prefix="PowerGlow",
                model="PowerOcean PowerGlow",
                via_sn=self.sn_inverter,
            )

            for key, value in element.items():
                if isinstance(value, dict):
                    continue

                if key in sens_select:
                    self._collect_sensor(
                        collector=collector,
                        device_sn=device_sn,
                        report=report,
                        key=key,
                        value=value,
                        device_info=device_info,
                    )

    def _handle_parallel_energy_stream(
        self,
        d: dict,
        report: str,
        collector,
    ):
        """Handle parallel energy stream data extraction."""
        para_list = d.get("paraEnergyStream", [])
        if not isinstance(para_list, list):
            LOGGER.warning("paraEnergyStream is not a list")
            return
        for device_data in para_list:
            raw_sn = device_data.get("devSn")
            device_sn = self._decode_sn(raw_sn) if raw_sn else None

            # Rolle bestimmen
            if device_sn == self.sn:
                suffix = DeviceRole.MASTER.value
            elif device_sn:
                suffix = DeviceRole.SLAVE.value
            else:
                device_sn = DeviceRole.ALL.value
                suffix = DeviceRole.ALL.value

            # DeviceInfo
            prefix = "Inverter"
            device_info = self._make_device_info(
                sn=device_sn,
                prefix=prefix,
                model=f"PowerOcean {prefix}",
                via_sn=self.sn_inverter if device_sn != DeviceRole.ALL.value else None,
            )
            report = f"{report} paraEnergyStream"
            for key, value in device_data.items():
                if isinstance(value, dict):
                    continue
                if key.endswith("Sn") and isinstance(value, str):
                    self._decode_sn(value)
                self._collect_sensor(
                    collector=collector,
                    device_sn=device_sn,
                    report=report,
                    key=key,
                    value=value,
                    device_info=device_info,
                    suffix="",
                )

    def _handle_standard_mode(
        self,
        d: dict,
        report: str,
        sens_select: list,
        suffix: str = "",
        *,
        collector,
    ):
        # spezielle Behandlung für 'data' Report
        report_id = "" if report == ReportMode.DEFAULT.value else f"{report}"
        device_sn, device_info = self._resolve_device_info(d, report_id)

        for key, raw_value in d.items():
            if key not in sens_select:
                continue
            if isinstance(raw_value, dict):
                continue

            value = raw_value
            self._collect_sensor(
                collector=collector,
                device_sn=device_sn,
                report=report_id,
                key=key,
                value=value,
                device_info=device_info,
                suffix="",
            )

    def _handle_edev_device(
        self,
        d: dict,
        *,
        report: str,
        sens_select: list,
        collector,
    ):
        """Generic handler for RE307_EDEV_SYS_REPORT using deep key lookup."""

        # SN über Deep Search holen
        device_sn = self._deep_get_by_key(d, "devSn")

        if not device_sn:
            return

        device_info = self._make_device_info(
            sn=device_sn,
            prefix="PowerPulse",
            model="PowerOcean PowerPulse",
            via_sn=self.sn_inverter,
        )

        # sens_select generisch auflösen
        for key in sens_select:
            value = device_sn if key == "devSn" else self._deep_get_by_key(d, key)

            if key == "devSn":
                continue
            if value is None:
                continue

            # nur primitive Werte erlauben
            if isinstance(value, (dict, list)):
                continue

            self._collect_sensor(
                collector=collector,
                device_sn=device_sn,
                report=report,
                key=key,
                value=value,
                device_info=device_info,
            )


class StructureCollector:
    def __init__(self):
        self.endpoints: dict[str, PowerOceanEndPoint] = {}

    def add(
        self,
        *,
        unique_id,
        device_sn,
        key,
        value=None,
        device_info: DeviceInfo,
        name,
        friendly_name,
    ):
        if unique_id in self.endpoints:
            return

        self.endpoints[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=device_sn,
            name=name,
            friendly_name=friendly_name,
            value=None,  # Struktur → kein Wert
            cls=SensorMetaHelper.get_class(key),
            description=SensorMetaHelper.get_description(key),
            icon=SensorMetaHelper.get_special_icon(key),
            device_info=device_info,
        )


class ValueCollector:
    def __init__(self):
        self.values: dict[str, float | int | str] = {}

    def add(
        self,
        *,
        unique_id,
        device_sn,
        key,
        value,
        device_info,
        name,
        friendly_name,
    ):
        if value is None:
            return
        self.values[unique_id] = value


class ApiResponseError(Exception):
    """Exception raised for API response errors."""


class ResponseTypeError(TypeError):
    """Exception raised when the response is not a dict."""

    def __init__(self, typename: str) -> None:
        """Initialize the exception with the provided type name."""
        super().__init__(f"Expected response to be a dict, got {typename}")


class AuthenticationFailedError(Exception):
    """Exception to indicate authentication failure."""


class FailedToExtractKeyError(Exception):
    """Exception raised when a required key cannot be extracted from a response."""

    def __init__(self, key: str, response: dict) -> None:
        """
        Initialize the exception with the missing key and response.

        Args:
            key (str): The key that could not be extracted.
            response (dict): The response dictionary where the key was missing.

        """
        self.key = key
        self.response = response
        super().__init__(f"Failed to extract key {key} from response: {response}")
