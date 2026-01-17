"""ecoflow.py: API for PowerOcean integration."""

import base64
import re
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

import requests
from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads

from .const import (
    _LOGGER,
    ISSUE_URL_ERROR_MESSAGE,
    LENGTH_BATTERIE_SN,
    USE_MOCKED_RESPONSE,
)


class ReportMode(Enum):
    """Enumeration for different report modes in the PowerOcean integration."""

    DEFAULT = "data"
    BATTERY = "BP_STA_REPORT"
    EMS = "EMS_HEARTBEAT"
    PARALLEL = "PARALLEL_ENERGY_STREAM_REPORT"
    EMS_CHANGE = "EMS_CHANGE_REPORT"


class DeviceRole(str, Enum):
    """Enumeration for device roles in the PowerOcean integration."""

    MASTER = "_master"
    SLAVE = "_slave"
    ALL = "_all"
    EMPTY = ""  # Used when no specific role is assigned


# Mock path to response.json file
mocked_response = Path("documentation/response_modified_po_dual.json")


# Better storage of PowerOcean endpoint
class PowerOceanEndPoint(NamedTuple):
    """
    Represents a PowerOcean endpoint with metadata and value.

    Attributes:
        internal_unique_id (str): Unique identifier for the endpoint.
        serial (str): Serial number of the device.
        name (str): Name of the endpoint.
        friendly_name (str): Human-readable name.
        value (object): Value of the endpoint.
        unit (str | None): Unit of measurement.
        description (str): Description of the endpoint.
        icon (str | None): Icon representing the endpoint.

    """

    internal_unique_id: str
    serial: str
    name: str
    friendly_name: str
    value: object
    unit: str | None
    description: str
    icon: str | None


class SensorMetaHelper:
    """Helper class for sensor metadata such as units, descriptions, and icons."""

    @staticmethod
    def get_unit(key: str) -> str | None:
        """Get unit from key name using a dictionary mapping."""
        unit_mapping = {
            "pwr": "W",
            "power": "W",
            "amp": "A",
            "soc": "%",
            "soh": "%",
            "vol": "V",
            "watth": "Wh",
            "energy": "Wh",
            "percent": "%",
            "volume": "L",
            "temp": "°C",
        }

        # Check for direct matches using dictionary lookup
        for suffix, unit in unit_mapping.items():
            if key.lower().endswith(suffix):
                return unit

        # Special case for "Generation" in key
        if "Generation" in key:
            return "kWh"

        return None  # Default if no match found

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


# ecoflow_api to detect device and get device info,
# fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow."""

    def __init__(
        self,
        serialnumber: str,
        username: str,
        password: str,
        variant: str,
        options: dict,
    ) -> None:
        """
        Initialize the Ecoflow API integration.

        Args:
            serialnumber (str): The serial number of the device.
            username (str): The Ecoflow account username.
            password (str): The Ecoflow account password.
            variant (str): The device variant.
            options (dict): Additional options for configuration.

        """
        self.sn = serialnumber
        self.sn_inverter = ""
        self.unique_id = serialnumber
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.ecoflow_variant = variant
        self.token = None
        self.device = None
        self.session = requests.Session()
        self.url_iot_app = "https://api.ecoflow.com/auth/login"
        self.url_user_fetch = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        self.datapointfile = Path(
            f"custom_components/powerocean/variants/{self.ecoflow_variant}.json"
        )
        self.options = options  # Store Home Assistant instance

    def get_device(self) -> dict:
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.27",  # Version vom Author.
            "build": "28",  # Version vom Author.
            "name": "PowerOcean",
            "features": "Photovoltaik",
        }

        return self.device

    def authorize(self) -> bool:
        """Authorize user and retrieve authentication token."""
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.ecoflow_username,
            "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        url = self.url_iot_app
        _LOGGER.info(f"Attempting to log in to EcoFlow API: {url}")
        response_data = None

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response_data = self.get_json_response(response)

            _LOGGER.debug(f"API Response: {response_data}")
            data_block = response_data.get("data")
            if not isinstance(data_block, dict) or "token" not in data_block:
                msg = "Missing or malformed 'data' block in response"
                _LOGGER.error(msg)
                raise AuthenticationFailedError(msg)

            self.token = data_block["token"]

        except requests.exceptions.RequestException as err:
            error_msg = f"Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error_msg + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error_msg) from err

        except KeyError as key_error:
            _LOGGER.error(
                f"Failed to extract key {key_error} from response: {response_data}"
            )
            msg = f"Missing {key_error} in response"
            raise FailedToExtractKeyError(msg, response_data or {}) from None

        _LOGGER.info("Successfully logged in.")
        self.get_device()
        return True

    def get_json_response(self, response: requests.Response) -> dict:
        """Parse JSON response and validate structure and status."""
        if not response.ok:
            msg = f"HTTP {response.status_code}: {response.text}"
            raise ApiResponseError(msg)

        try:
            response_data = json_loads(response.text)
        except ValueError as error:
            msg = f"Failed to parse JSON: {response.text} — Error: {error}"
            raise json_loads.JsonParseError(msg) from error

        if not isinstance(response_data, dict) or "message" not in response_data:
            msg = f"'message' key missing in response: {response_data}"
            raise KeyError(msg)

        message = response_data.get("message")
        if not isinstance(message, str) or message.lower() != "success":
            msg = f"API Error: {message}"
            raise ApiResponseError(msg)

        if "data" not in response_data:
            msg = f"'data' key missing or invalid: {response_data}"
            raise KeyError(msg)

        return response_data

    # Fetch the data from the PowerOcean device, which then constitues the Sensors
    def fetch_data(self) -> dict:
        """Fetch data from Url."""
        url = self.url_user_fetch
        try:
            headers = {
                "authorization": f"Bearer {self.token}",
                "user-agent": "Firefox/133.0",
                "product-type": self.ecoflow_variant,
            }
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)

            if USE_MOCKED_RESPONSE:
                try:
                    with Path.open(mocked_response, "r", encoding="utf-8") as file:
                        response = json_loads(file.read())
                except FileNotFoundError:
                    _LOGGER.debug(
                        f"Mocked response file not present: {mocked_response}"
                    )
            # Log the response for debugging or development purposes
            _LOGGER.debug(f"{response}")
            flattened_data = Ecoflow.flatten_json(response)  # noqa: F841
            # _LOGGER.debug(f"Flattened data: {flattened_data}")  # noqa: ERA001

            # Ensure response is a dictionary before passing to _get_sensors
            if isinstance(response, dict):
                return self._get_sensors(response)
            raise ResponseTypeError(type(response).__name__)

        except ConnectionError as err:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error) from err

    def __read_json_file(self) -> dict:
        try:
            with self.datapointfile.open("r", encoding="utf-8") as file:
                data = json_loads(file.read())
                if isinstance(data, dict):
                    return data
                _LOGGER.error(
                    f"JSON content is not a dict in file: {self.datapointfile}"
                )
                return {}
        except FileNotFoundError:
            _LOGGER.error(f"File not found: {self.datapointfile}")
        except json_loads.JSONDecodeError:
            _LOGGER.error(f"Error decoding JSON in file: {self.datapointfile}")
        return {}

    def _get_reports(self) -> list:
        """Retrieve report selection from JSON file."""
        reports = self.__read_json_file()
        if isinstance(reports, dict):
            return list(reports.keys())
        return []

    def _get_sens_select(self, report: str) -> list:
        """Retrieve sensor selection from JSON file."""
        datapoints = self.__read_json_file()
        if isinstance(datapoints, dict):
            value = datapoints.get(report, [])
        if isinstance(value, list):
            return value
        # If value is not a list, return an empty list
        return []

    def _create_sensor(self, endpoint: PowerOceanEndPoint) -> PowerOceanEndPoint:
        return endpoint

    @staticmethod
    def flatten_json(y: Any) -> dict:
        """
        Flatten a nested JSON object into a flat dictionary.

        Args:
            y: The JSON object (dict or list) to flatten.

        Returns:
            dict: A flattened dictionary with keys representing the path to each value.

        """
        out = {}

        def flatten(x: Any, name: str = "") -> None:
            if isinstance(x, dict):
                for a in x:
                    flatten(x[a], f"{name}{a}_")
            elif isinstance(x, list):
                for i, a in enumerate(x):
                    flatten(a, f"{name}{i}_")
            else:
                out[name[:-1]] = x

        flatten(y)
        return out

    def _get_sensors(self, response: dict) -> dict:
        sensors = {}  # start with empty dict
        self.sn_inverter = self.sn
        # error handling for response
        data = response.get("data")
        if not isinstance(data, dict):
            _LOGGER.error("No 'data' in response.")
            return sensors  # return empty dict if no data

        # Handle generic 'data' report
        reports_data = self._get_reports()
        reports = []
        report = ReportMode.DEFAULT.value
        if report in reports_data:
            # Special case for 'data' report
            sensors.update(
                self._extract_sensors_from_report(
                    response,
                    sensors,
                    report,
                )
            )
            reports = [x for x in reports_data if x != "data"]
        _LOGGER.debug(f"Reports to look for: {reports}")

        # Dual inverter installation
        if "parallel" in data:
            response_parallel = data["parallel"]
            inverters = list(response_parallel.keys())

            for element in inverters or [self.sn]:
                self.sn_inverter = element
                response_base = response_parallel.get(element, {})
                _LOGGER.debug(f"Processing inverter: {element}")
                suffix = (
                    DeviceRole.MASTER.value
                    if element == self.sn
                    else DeviceRole.SLAVE.value
                )
                for report in reports:
                    # Besonderheit: JTS1_ENERGY_STREAM_REPORT  # noqa: ERA001
                    if "ENERGY_STREAM_REPORT" in report:
                        report_key = re.sub(r"JTS1_", "JTS1_PARALLEL_", report)
                    else:
                        report_key = report

                    sensors.update(
                        self._extract_sensors_from_report(
                            response_base,
                            sensors,
                            report_key,
                            suffix=suffix,
                            battery_mode=ReportMode.BATTERY.value in report_key,
                            ems_heartbeat_mode=ReportMode.EMS.value in report_key,
                            parallel_energy_stream_mode=ReportMode.PARALLEL.value
                            in report_key,
                        )
                    )
        # Single inverter installation
        elif "quota" in data:
            response_base = data["quota"]
            for report in reports:
                sensors.update(
                    self._extract_sensors_from_report(
                        response_base,
                        sensors,
                        report,
                        battery_mode=ReportMode.BATTERY.value in report,
                        ems_heartbeat_mode=ReportMode.EMS.value in report,
                    )
                )
        else:
            _LOGGER.warning(
                "Neither 'quota' nor 'parallel' inverter data found in response."
            )
        return sensors

    def _extract_sensors_from_report(  # noqa: PLR0913
        self,
        response: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        suffix: str = "",
        *,
        battery_mode: bool = False,
        ems_heartbeat_mode: bool = False,
        parallel_energy_stream_mode: bool = False,
    ) -> dict[str, PowerOceanEndPoint]:
        """
        Allgemeine Methode zum Extrahieren von Sensoren aus einem Report.

        Args:
            response: API Antwort als dict
            sensors: bisher gesammelte Sensoren (wird erweitert)
            report: Name des Reports im JSON
            suffix: Suffix, das an die Namen der Sensoren angehängt wird
                (z.B. für Master/Slave).
            battery_mode: Wenn True, werden Batteriedaten speziell behandelt
            ems_heartbeat_mode: Wenn True, wird die spezielle EMS-Heartbeat-Verarbeitung
            parallel_energy_stream_mode: Wenn True, wird die spezielle Verarbeitung
                für parallele Energie-Streams verwendet.

        Returns:
            Erweitertes sensors dict mit neuen Sensoren

        """
        # Report-Key ggf. anpassen
        if report not in response:
            report_to_log = report
            report = re.sub(r"JTS1_", "RE307_", report)

        d = response.get(report)
        if not d:
            _LOGGER.debug(f"Configured report '{report_to_log}' not in response.")
            return sensors

        sens_select = self._get_sens_select(report)
        if battery_mode:
            return self._handle_battery_mode(d, sensors, report, sens_select, suffix)
        # EMS Heartbeat Mode
        if ems_heartbeat_mode:
            return self._handle_ems_heartbeat_mode(
                d, sensors, report, sens_select, suffix
            )
        # Parallel Energy Stream Mode
        if parallel_energy_stream_mode:
            return self._handle_parallel_energy_stream(
                d, sensors, report, sens_select, suffix
            )
        # Standardverarbeitung
        return self._handle_standard_mode(d, sensors, report, sens_select, suffix)

    def _handle_battery_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        """Handle battery mode data extraction."""
        # Batteriedaten: d enthält JSON Strings pro Batterie
        keys = list(d.keys())
        batts = [s for s in keys if len(s) > LENGTH_BATTERIE_SN]
        prefix = "bpack"
        for ibat, bat in enumerate(reversed(batts)):
            name = f"{prefix}{ibat + 1}_"
            raw_data = d.get(bat)
            d_bat = self._parse_battery_data(raw_data)
            if isinstance(d_bat, dict):
                for key, value in d_bat.items():
                    if key in sens_select:
                        unique_id = f"{self.sn_inverter}_{report}_{bat}_{key}"
                        sensors[unique_id] = self._create_sensor(
                            PowerOceanEndPoint(
                                internal_unique_id=unique_id,
                                serial=f"{self.sn_inverter}",
                                name=f"{self.sn_inverter}_{name}{key}{suffix}",
                                friendly_name=f"{name}{key}{suffix}",
                                value=value,
                                unit=SensorMetaHelper.get_unit(key),
                                description=f"{name}{SensorMetaHelper.get_description(key)}",
                                icon=SensorMetaHelper.get_special_icon(key),
                            )
                        )
            else:
                _LOGGER.error(f"Battery data for '{bat}' is not a dict: {type(d_bat)}")
        return sensors

    def _parse_battery_data(self, raw_data: dict | str | None) -> dict | None:
        if isinstance(raw_data, str):
            data = json_loads(raw_data)
            if isinstance(data, dict):
                return data
            _LOGGER.error(f"Parsed battery data is not a dict: {type(data)}")
            return None
        if isinstance(raw_data, dict):
            return raw_data
        _LOGGER.error(f"Unexpected battery data type: {type(raw_data)}")
        return None

    def _handle_ems_heartbeat_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        # EMS Heartbeat: ggf. verschachtelte Strukturen, spezielle Behandlung
        for key, value in d.items():
            if key in sens_select:
                unique_id = f"{self.sn_inverter}_{report}_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=f"{self.sn_inverter}",
                        name=f"{self.sn_inverter}_{key}{suffix}",
                        friendly_name=f"{key}{suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=None,
                    )
                )
        # Besonderheiten Phasen
        phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
        if phases[1] in d:
            for _, phase in enumerate(phases):
                for key, value in d[phase].items():
                    name = f"{phase}_{key}"
                    unique_id = f"{self.sn_inverter}_{report}_{name}"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=f"{self.sn_inverter}",
                            name=f"{self.sn_inverter}_{name}{suffix}",
                            friendly_name=f"{name}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=None,
                        )
                    )
        # Besonderheit mpptPv
        if "mpptHeartBeat" in d:
            n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])
            for i in range(n_strings):
                for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                    unique_id = (
                        f"{self.sn_inverter}_{report}_mpptHeartBeat_mpptPv{i + 1}_{key}"
                    )
                    special_icon = None
                    if key.endswith("amp"):
                        special_icon = "mdi:current-dc"
                    elif key.endswith("pwr"):
                        special_icon = "mdi:solar-power"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=f"{self.sn_inverter}",
                            name=f"{self.sn_inverter}_mpptPv{i + 1}_{key}{suffix}",
                            friendly_name=f"mpptPv{i + 1}_{key}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=special_icon,
                        )
                    )
            # Gesamtleistung mpptPv
            total_power = sum(
                d["mpptHeartBeat"][0]["mpptPv"][i].get("pwr", 0)
                for i in range(n_strings)
            )
            unique_id = f"{self.sn_inverter}_{report}_mpptHeartBeat_mpptPv_pwrTotal"
            sensors[unique_id] = self._create_sensor(
                PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=f"{self.sn_inverter}",
                    name=f"{self.sn_inverter}_mpptPv_pwrTotal{suffix}",
                    friendly_name=f"mpptPv_pwrTotal{suffix}",
                    value=total_power,
                    unit="W",
                    description="Solarertrag aller Strings",
                    icon="mdi:solar-power",
                )
            )
        return sensors

    def _handle_parallel_energy_stream(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,  # noqa: ARG002
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        """Handle parallel energy stream data extraction."""
        if "paraEnergyStream" in d:
            para_list = d.get("paraEnergyStream", [])
            for device_data in para_list:
                dev_sn = device_data.get("devSn")
                if not dev_sn or len(dev_sn) < LENGTH_BATTERIE_SN:
                    dev_sn = ""  # Fallback für unbekannte Seriennummer
                _LOGGER.debug(f"Processing parallel dev_sn: {dev_sn}")
                if dev_sn == self.sn:
                    suffix = DeviceRole.MASTER.value
                elif dev_sn != "":
                    suffix = DeviceRole.SLAVE.value
                else:
                    suffix = DeviceRole.ALL.value

                for key, value in device_data.items():
                    unique_id = f"{dev_sn}_{report}_paraEnergyStream_{key}"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=dev_sn,
                            name=f"{dev_sn}_{key}{suffix}",
                            friendly_name=f"{key}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=SensorMetaHelper.get_special_icon(key),
                        )
                    )
        return sensors

    def _handle_standard_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        # Standardverarbeitung: einfache key-value Paare
        report_string = f"_{report}"
        # spezielle Behandlung für 'data' Report
        if report == ReportMode.DEFAULT.value:
            report_string = ""
        for key, value in d.items():
            if key in sens_select and not isinstance(value, dict):
                unique_id = f"{self.sn_inverter}{report_string}_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=f"{self.sn_inverter}",
                        name=f"{self.sn_inverter}_{key}{suffix}",
                        friendly_name=f"{key}{suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=SensorMetaHelper.get_special_icon(key),
                    )
                )
        return sensors


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
