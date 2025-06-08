"""ecoflow.py: API for PowerOcean integration."""

import base64
import re
from pathlib import Path
from typing import NamedTuple

import requests
from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads

from .const import _LOGGER, DOMAIN, ISSUE_URL_ERROR_MESSAGE

# Mock path to response.json file
mocked_response = Path("documentation/response.json")


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


# ecoflow_api to detect device and get device info, fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow."""

    def __init__(
        self, serialnumber: str, username: str, password: str, variant: str
    ) -> None:
        self.sn = serialnumber
        self.unique_id = serialnumber
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.ecoflow_variant = variant
        self.token = None
        self.device = None
        self.session = requests.Session()
        self.url_iot_app = "https://api.ecoflow.com/auth/login"
        self.url_user_fetch = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        self.use_mocked_response = False  # Set to True to use mocked response

    def get_device(self) -> dict:
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.27",  # TODO: woher bekommt man diese Info?
            "build": "28",  # TODO: wo finde ich das?
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

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response_data = self.get_json_response(response)
            _LOGGER.debug(f"API Response: {response_data}")

            self.token = response_data.get("data", {}).get("token")
            if not self.token:
                msg = "Missing 'token' in response data"
                _LOGGER.error(msg)
                raise AuthenticationFailed(msg)

        except ConnectionError as err:
            error_msg = f"Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error_msg + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error_msg) from err

        except KeyError as key_error:
            _LOGGER.error(
                f"Failed to extract key {key_error} from response: "
                f"{locals().get('response_data', {})}"
            )
            msg = f"Missing {key_error} in response"
            raise FailedToExtractKeyError(
                msg, locals().get("response_data", {})
            ) from None

        else:
            _LOGGER.info("Successfully logged in.")
            self.get_device()
            return True

    def get_json_response(self, request: requests.Response) -> dict:
        """Parse JSON response and validate message status."""
        if request.status_code != 200:
            msg = f"HTTP {request.status_code}: {request.text}"
            raise Exception(msg)

        try:
            response_data = json_loads(request.text)
        except ValueError as error:
            msg = f"Failed to parse JSON response: {request.text} — Error: {error}"
            raise json_loads.JsonParseError(request.text, error) from error

        # Validate message key
        message = response_data.get("message")
        if message is None:
            raise KeyError(f"'message' key missing in response: {response_data}")

        if message.lower() != "success":
            raise Exception(f"API Error: {message}")

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

            if self.use_mocked_response:
                try:
                    with Path.open(mocked_response, "r", encoding="utf-8") as file:
                        response = json_loads(file.read())
                except FileNotFoundError:
                    _LOGGER.debug(
                        f"Mocked response file not present: {mocked_response}"
                    )

            # Debugging output for the response
            _LOGGER.debug(f"{response}")
            # Ensure response is a dictionary before passing to _get_sensors
            if isinstance(response, dict):
                return self._get_sensors(response)
            raise ResponseTypeError(type(response).__name__)

        except ConnectionError as err:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error) from err

    def __get_unit(self, key: str) -> str | None:
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
        }

        # Check for direct matches using dictionary lookup
        for suffix, unit in unit_mapping.items():
            if key.lower().endswith(suffix):
                return unit

        # Special case for "Generation" in key
        if "Generation" in key:
            return "kWh"

        # Special case for keys ending with "Temp"
        if key.endswith("Temp"):
            return "°C"

        return None  # Default if no match found

    def __get_description(self, key: str) -> str:
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

    def __get_special_icon(self, key: str) -> str | None:
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

    def __get_sens_select(self, report: str) -> dict:
        """Retrieve sensor selection from JSON file."""
        datapointfile = Path(
            f"custom_components/{DOMAIN}/variants/{self.ecoflow_variant}.json"
        )

        try:
            with datapointfile.open("r", encoding="utf-8") as file:
                datapoints = json_loads(
                    file.read()
                )  # Directly load JSON without reading as a string

            return datapoints.get(report, {})  # Use .get() to avoid KeyErrors

        except FileNotFoundError:
            _LOGGER.error(f"File not found: {datapointfile}")
            return {}

        except AttributeError:
            _LOGGER.error(f"Error decoding JSON in file: {datapointfile}")
            return {}

    def _get_sensors(self, response: dict) -> dict:
        sensors = {}  # start with empty dict
        # error handling for response
        data = response.get("data")
        if not isinstance(data, dict):
            _LOGGER.error("No 'data' in response.")
            return sensors  # return empty dict if no data

        # get sensors from response['data']
        sensors.update(self.__get_sensors_data(response, sensors))

        for report_type in [
            "JTS1_ENERGY_STREAM_REPORT",
            "JTS1_EMS_CHANGE_REPORT",
            "JTS1_EVCHARGING_REPORT",
        ]:
            sensors.update(
                self.__get_sensors_from_response(response, sensors, report_type)
            )

        # get info from batteries  => JTS1_BP_STA_REPORT
        sensors.update(
            self.__get_sensors_battery(response, sensors, "JTS1_BP_STA_REPORT")
        )

        # get info from PV strings  => JTS1_EMS_HEARTBEAT
        sensors.update(
            self.__get_sensors_ems_heartbeat(response, sensors, "JTS1_EMS_HEARTBEAT")
        )

        return sensors

    def __get_sensors_data(self, response: dict, sensors: dict) -> dict:
        d = response["data"]

        sens_select = self.__get_sens_select("data")

        sensors = {}  # start with empty dict
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                if not isinstance(value, dict):
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{key}"

                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=self.__get_description(key),
                        icon=self.__get_special_icon(key),
                    )

        return sensors

    def __get_sensors_from_response(
        self, response: dict, sensors: dict, report: str
    ) -> dict:
        """Get sensors from response data based on report_data."""
        if report not in response["data"]["quota"]:
            report = re.sub(r"JTS1_", "RE307_", report)

        try:
            d = response["data"]["quota"][report]
            # d = response.get("data", {}).get("quota", {}).get(report, {})
        except KeyError:
            _LOGGER.debug(
                f"Missing report in response: {re.sub(r'^[^_]+_', '', report)}"
            )
            return sensors

        try:
            # remove prefix from report
            report_data = re.sub(r"^[^_]+_", "", report)
            # get sensors to select from datapoints.json
            sens_select = self.__get_sens_select(report_data)

            # data = {}
            for key, value in d.items():
                if key in sens_select:  # use only sensors in sens_select
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{report}_{key}"
                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=self.__get_description(key),
                        icon=self.__get_special_icon(key),
                    )
            # dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(
                f"Report {report_data} not found in {self.ecoflow_variant}.json."
            )
        return sensors

    def __get_sensors_battery(self, response: dict, sensors: dict, report: str) -> dict:
        if report not in response["data"]["quota"]:
            report = re.sub(r"JTS1_", "RE307_", report)

        try:
            d = response["data"]["quota"][report]
            # d = response.get("data", {}).get("quota", {}).get(report, {})
        except KeyError:
            _LOGGER.warning(f"Missing report: {report}")
            return sensors

        try:
            report_data = re.sub(r"^[^_]+_", "", report)
            keys = list(d.keys())

            # loop over N batteries:
            batts = [s for s in keys if len(s) > 12]
            bat_sens_select = self.__get_sens_select(report_data)

            # data = {}
            prefix = "bpack"
            for ibat, bat in enumerate(batts):
                name = prefix + "%i_" % (ibat + 1)
                d_bat = json_loads(d[bat])
                for key, value in d_bat.items():
                    if key in bat_sens_select:
                        # default uid, unit and descript
                        unique_id = f"{self.sn}_{report}_{bat}_{key}"
                        # unique_id = f"{bat}_{report}_{key}"
                        description_tmp = f"{name}{self.__get_description(key)}"
                        sensors[unique_id] = PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=self.sn,
                            name=f"{self.sn}_{name}{key}",
                            friendly_name=name + key,
                            value=value,
                            unit=self.__get_unit(key),
                            description=description_tmp,
                            icon=self.__get_special_icon(key),
                        )

            # dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(f"Report {report} not found in datapoints.json.")
        return sensors

    def __get_sensors_ems_heartbeat(
        self, response: dict, sensors: dict, report: str
    ) -> dict:
        if report not in response["data"]["quota"]:
            report = re.sub(r"JTS1_", "RE307_", report)

        try:
            d = response["data"]["quota"][report]
            # d = response.get("data", {}).get("quota", {}).get(report, {})
        except KeyError:
            _LOGGER.warning(f"Missing report: {report}")
            return sensors

        try:
            report_data = re.sub(r"^[^_]+_", "", report)
            sens_select = self.__get_sens_select(report_data)

            # data = {}
            for key, value in d.items():
                if key in sens_select:
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{report}_{key}"
                    description_tmp = self.__get_description(key)
                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=description_tmp,
                        icon=None,
                    )

            # special for phases
            phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
            if phases[1] in d:
                for i, phase in enumerate(phases):
                    for key, value in d[phase].items():
                        name = f"{phase}_{key}"
                        unique_id = f"{self.sn}_{report}_{name}"

                        sensors[unique_id] = PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=self.sn,
                            name=f"{self.sn}_{name}",
                            friendly_name=f"{name}",
                            value=value,
                            unit=self.__get_unit(key),
                            description=self.__get_description(key),
                            icon=None,
                        )

            # special for mpptPv
            if "mpptHeartBeat" in d:
                n_strings = len(
                    d["mpptHeartBeat"][0]["mpptPv"]
                )  # TODO: auch als Sensor?
                mpptpvs = []
                for i in range(1, n_strings + 1):
                    mpptpvs.append(f"mpptPv{i}")
                mpptPv_sum = 0.0
                for i, mpptpv in enumerate(mpptpvs):
                    for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                        unique_id = f"{self.sn}_{report}_mpptHeartBeat_{mpptpv}_{key}"
                        special_icon = None
                        if key.endswith("amp"):
                            special_icon = "mdi:current-dc"
                        if key.endswith("pwr"):
                            special_icon = "mdi:solar-power"

                        sensors[unique_id] = PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=self.sn,
                            name=f"{self.sn}_{mpptpv}_{key}",
                            friendly_name=f"{mpptpv}_{key}",
                            value=value,
                            unit=self.__get_unit(key),
                            description=self.__get_description(key),
                            icon=special_icon,
                        )
                        # sum power of all strings
                        if key == "pwr":
                            mpptPv_sum += value

                # create total power sensor of all strings
                name = "mpptPv_pwrTotal"
                unique_id = f"{self.sn}_{report}_mpptHeartBeat_{name}"
                sensors[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{name}",
                    friendly_name=f"{name}",
                    value=mpptPv_sum,
                    unit=self.__get_unit(key),
                    description="Solarertrag aller Strings",
                    icon="mdi:solar-power",
                )

            # dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(f"Report {report} not found in datapoints.json.")
        return sensors


class ResponseTypeError(TypeError):
    """Exception raised when the response is not a dict."""

    def __init__(self, typename):
        super().__init__(f"Expected response to be a dict, got {typename}")


class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""


class FailedToExtractKeyError(Exception):
    """Exception raised when a required key cannot be extracted from a response."""

    def __init__(self, key, response):
        self.key = key
        self.response = response
        super().__init__(f"Failed to extract key {key} from response: {response}")
