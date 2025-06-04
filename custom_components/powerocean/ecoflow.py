"""ecoflow.py: API for PowerOcean integration."""

import base64
import re
from collections import namedtuple
from pathlib import Path

import requests
from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads
from requests.exceptions import RequestException

from .const import _LOGGER, DOMAIN, ISSUE_URL_ERROR_MESSAGE

# Mock path to response.json file
mocked_response = Path("documentation/response.json")

# Better storage of PowerOcean endpoint
PowerOceanEndPoint = namedtuple(
    "PowerOceanEndPoint",
    "internal_unique_id, serial, name, friendly_name, value, unit, description, icon",
)


# ecoflow_api to detect device and get device info, fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow"""

    def __init__(self, serialnumber: str, username: str, password: str, variant: str):
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

    def get_device(self):
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.25",  # TODO: woher bekommt man diese Info?
            "build": "8",  # TODO: wo finde ich das?
            "name": "PowerOcean",
            "features": "Photovoltaik",
        }

        return self.device

    def authorize(self):
        """Function authorize"""
        auth_ok = False  # default
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.ecoflow_username,
            "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            url = self.url_iot_app
            _LOGGER.info("Login to EcoFlow API %s", {url})
            request = requests.post(url, json=data, headers=headers, timeout=10)
            response = self.get_json_response(request)
            _LOGGER.debug(f"{response}")

        except ConnectionError:
            error = f"Unable to connect to {self.url_iot_app}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

        try:
            self.token = response["data"]["token"]
            # self.user_id = response["data"]["user"]["userId"]
            # user_name = response["data"]["user"].get("name", "<no user name>")
            auth_ok = True
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from response: {response}")

        _LOGGER.info("Successfully logged in.")

        self.get_device()  # collect device info

        return auth_ok

    def get_json_response(self, request):
        """Function get json response"""
        if request.status_code != 200:
            raise Exception(
                f"Got HTTP status code {request.status_code}: {request.text}"
            )
        try:
            response = json_loads(request.text)
            response_message = response["message"]
        except KeyError as key:
            raise Exception(
                f"Failed to extract key {key} from {json_loads(request.text)}"
            )
        except Exception as error:
            raise Exception(f"Failed to parse response: {request.text} Error: {error}")

        if response_message.lower() != "success":
            raise Exception(f"{response_message}")

        return response

    # Fetch the data from the PowerOcean device, which then constitues the Sensors
    def fetch_data(self):
        """Fetch data from Url."""
        url = self.url_user_fetch
        try:
            headers = {
                "authorization": f"Bearer {self.token}",
                # "accept": "application/json, text/plain, */*",
                "user-agent": "Firefox/133.0",
                # "platform": "web",
                "product-type": "83",
            }
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)

            try:
                # TESTING!!! create response file and use it as data
                with Path.open(mocked_response, "r", encoding="utf-8") as datei:
                    response = json_loads(datei.read())
            except FileExistsError:
                error = f"Data from: {mocked_response}"
                _LOGGER.warning(error)
            except FileNotFoundError:
                error = f"Responsefile not present: {mocked_response}"
                _LOGGER.debug(error)

            _LOGGER.debug(f"{response}")
            return self._get_sensors(response)

        except ConnectionError:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

        except RequestException as e:
            error = f"RequestException in fetch_data: Error while fetching data from {url}: {e}"
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

    def __get_unit(self, key: str) -> str | None:
        """Get unit from key name using a dictionary mapping."""
        unit_mapping = {
            "pwr": "W",
            "Pwr": "W",
            "Power": "W",
            "amp": "A",
            "Amp": "A",
            "soc": "%",
            "Soc": "%",
            "soh": "%",
            "Soh": "%",
            "vol": "V",
            "Vol": "V",
            "Watth": "Wh",
            "Energy": "Wh",
        }

        # Check for direct matches using dictionary lookup
        for suffix, unit in unit_mapping.items():
            if key.endswith(suffix):
                return unit

        # Special case for "Generation" in key
        if "Generation" in key:
            return "kWh"

        # Special case for keys starting with "bpTemp"
        if key.startswith("bpTemp"):
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

    def __get_sens_select(self, report):
        # open File an read
        # Dict with entity names to use
        datapointfile = Path(
            f"custom_components/{DOMAIN}/variants/{self.ecoflow_variant}.json"
        )
        with Path.open(datapointfile) as file:
            datapoints = json_loads(file.read())

        return datapoints[report]

    def _get_sensors(self, response):
        # get sensors from response['data']
        sensors = self.__get_sensors_data(response)

        # get sensors from 'JTS1_ENERGY_STREAM_REPORT'
        # sensors = self.__get_sensors_energy_stream(response, sensors)
        sensors = self.__get_sensors_from_response(
            response, sensors, "JTS1_ENERGY_STREAM_REPORT"
        )

        # get sensors from 'JTS1_EMS_CHANGE_REPORT'
        # sensors = self.__get_sensors_ems_change(response, sensors)
        sensors = self.__get_sensors_from_response(
            response, sensors, "JTS1_EMS_CHANGE_REPORT"
        )

        # get info from batteries  => JTS1_BP_STA_REPORT
        sensors = self.__get_sensors_battery(response, sensors)

        # get info from PV strings  => JTS1_EMS_HEARTBEAT
        sensors = self.__get_sensors_ems_heartbeat(response, sensors)

        return sensors

    def __get_sensors_data(self, response):
        d = response["data"].copy()

        sens_select = self.__get_sens_select("data")

        sensors = dict()  # start with empty dict
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                if not isinstance(value, dict):
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{key}"
                    special_icon = None
                    if key == "mpptPwr":
                        special_icon = "mdi:solar-power"
                    if key == "online":
                        special_icon = "mdi:cloud-check"
                    if key == "sysGridPwr":
                        special_icon = "mdi:transmission-tower-import"
                    if key == "sysLoadPwr":
                        special_icon = "mdi:home-import-outline"

                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=self.__get_description(key),
                        icon=special_icon,
                    )

        return sensors

    def __get_sensors_energy_stream(self, response, sensors):
        report = "JTS1_ENERGY_STREAM_REPORT"
        d = response["data"]["quota"][report]
        sens_select = self.__get_sens_select(report)

        data = {}
        for key, value in d.items():
            if key in sens_select:
                # default uid, unit and descript
                unique_id = f"{self.sn}_{report}_{key}"
                special_icon = None
                if key.startswith(("pv1", "pv2")):
                    special_icon = "mdi:solar-power"
                description_tmp = self.__get_description(key)
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{key}",
                    friendly_name=key,
                    value=value,
                    unit=self.__get_unit(key),
                    description=description_tmp,
                    icon=special_icon,
                )

        dict.update(sensors, data)

        return sensors

    def __get_sensors_ems_change(self, response, sensors):
        report = "JTS1_EMS_CHANGE_REPORT"
        d = response["data"]["quota"][report]

        sens_select = self.__get_sens_select(report)

        # add mppt Warning/Fault Codes
        keys = d.keys()
        r = re.compile("mppt.*Code")
        wfc = list(filter(r.match, keys))  # warning/fault code keys
        sens_select += wfc

        data = {}
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                # default uid, unit and descript
                unique_id = f"{self.sn}_{report}_{key}"

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{key}",
                    friendly_name=key,
                    value=value,
                    unit=self.__get_unit(key),
                    description=self.__get_description(key),
                    icon=None,
                )
        dict.update(sensors, data)

        return sensors

    def __get_sensors_from_response(self, response, sensors, report_data):
        """Get sensors from response data based on report_data."""
        if report_data not in response["data"]["quota"]:
            report_data = re.sub(r"JTS1_", "RE307_", report_data)
        d = response["data"]["quota"][report_data]

        try:
            # remove prefix from report_data
            report = re.sub(r"^[^_]+_", "", report_data)
            # get sensors to select from datapoints.json
            sens_select = self.__get_sens_select(report)

            data = {}
            for key, value in d.items():
                if key in sens_select:  # use only sensors in sens_select
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{report}_{key}"
                    special_icon = None
                    if key.startswith(("pv1", "pv2")):
                        special_icon = "mdi:solar-power"

                    data[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=self.__get_description(key),
                        icon=special_icon,
                    )
            dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(f"Report {report} not found in datapoints.json.")
        return sensors

    def __get_sensors_battery(self, response, sensors):
        report = "JTS1_BP_STA_REPORT"
        if report not in response["data"]["quota"]:
            report = "RE307_BP_STA_REPORT"
        d = response["data"]["quota"][report]

        try:
            report = re.sub(r"^[^_]+_", "", report)
            keys = list(d.keys())

            # loop over N batteries:
            batts = [s for s in keys if len(s) > 12]
            bat_sens_select = self.__get_sens_select(report)

            data = {}
            prefix = "bpack"
            for ibat, bat in enumerate(batts):
                name = prefix + "%i_" % (ibat + 1)
                d_bat = json_loads(d[bat])
                for key, value in d_bat.items():
                    if key in bat_sens_select:
                        # default uid, unit and descript
                        # unique_id = f"{self.sn}_{report}_{bat}_{key}"
                        unique_id = f"{bat}_{report}_{key}"
                        description_tmp = f"{name}" + self.__get_description(key)
                        special_icon = None
                        if key == "bpAmp":
                            special_icon = "mdi:current-dc"
                        data[unique_id] = PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=self.sn,
                            name=f"{self.sn}_{name + key}",
                            friendly_name=name + key,
                            value=value,
                            unit=self.__get_unit(key),
                            description=description_tmp,
                            icon=special_icon,
                        )
                # compute mean temperature of cells
                key = "bpTemp"
                temp = d_bat[key]
                value = sum(temp) / len(temp)
                # unique_id = f"{self.sn}_{report}_{bat}_{key}"
                unique_id = f"{bat}_{report}_{key}"
                description_tmp = f"{name}" + self.__get_description(key)
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    # name=f"{self.sn}_{name + key}",
                    name=f"{bat}_{name + key}",
                    friendly_name=name + key,
                    value=value,
                    unit=self.__get_unit(key),
                    description=description_tmp,
                    icon=None,
                )

            dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(f"Report {report} not found in datapoints.json.")
        return sensors

    def __get_sensors_ems_heartbeat(self, response, sensors):
        report = "JTS1_EMS_HEARTBEAT"
        if report not in response["data"]["quota"]:
            report = "RE307_EMS_HEARTBEAT"
        d = response["data"]["quota"][report]

        try:
            report = re.sub(r"^[^_]+_", "", report)
            sens_select = self.__get_sens_select(report)

            data = {}
            for key, value in d.items():
                if key in sens_select:
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{report}_{key}"
                    description_tmp = self.__get_description(key)
                    data[unique_id] = PowerOceanEndPoint(
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
                        name = phase + "_" + key
                        unique_id = f"{self.sn}_{report}_{name}"

                        data[unique_id] = PowerOceanEndPoint(
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

                        data[unique_id] = PowerOceanEndPoint(
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

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{name}",
                    friendly_name=f"{name}",
                    value=mpptPv_sum,
                    unit=self.__get_unit(key),
                    description="Solarertrag aller Strings",
                    icon="mdi:solar-power",
                )

            dict.update(sensors, data)

        except KeyError:
            _LOGGER.error(f"Report {report} not found in datapoints.json.")
        return sensors


class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
