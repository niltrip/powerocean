"""ecoflow.py: API for PowerOcean integration."""

import requests
import base64
import re
from collections import namedtuple
from requests.exceptions import RequestException

from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads

from .const import _LOGGER, ISSUE_URL_ERROR_MESSAGE

# Mock Pfad zu validem json File
# os.path.join("/workspaces/powerocean/documentation/response_modified_dcfit_2025.json")
FAKEDATA = None


# Better storage of PowerOcean endpoint
PowerOceanEndPoint = namedtuple(
    "PowerOceanEndPoint",
    "internal_unique_id, serial, name, friendly_name, value, unit, description, icon",
)


# ecoflow_api to detect device and get device info, fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow"""

    def __init__(self, serialnumber, username, password):
        self.sn = serialnumber
        self.unique_id = serialnumber
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.token = None
        self.device = None
        self.session = requests.Session()
        self.url_iot_app = "https://api.ecoflow.com/auth/login"
        self.url_user_fetch = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        # self.authorize()  # authorize user and get device details

    def get_device(self):
        """Function get device"""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.15",  # TODO: woher bekommt man diese Info?
            "build": "6",  # TODO: wo finde ich das?
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
            request = requests.post(url, json=data, headers=headers)
            response = self.get_json_response(request)

        except ConnectionError:
            error = f"Unable to connect to {self.url_iot_app}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

        try:
            self.token = response["data"]["token"]
            self.user_id = response["data"]["user"]["userId"]
            user_name = response["data"]["user"].get("name", "<no user name>")
            auth_ok = True
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from response: {response}")

        _LOGGER.info("Successfully logged in: %s", {user_name})

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
        """Function fetch data from Url."""
        # curl 'https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}}' \
        # -H 'authorization: Bearer {self.token}'

        url = self.url_user_fetch
        try:
            headers = {
                "authorization": f"Bearer {self.token}",
                "user-agent": "Firefox/133.0",
            }
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)
            # TESTING!!! create response file and use it as data
            if FAKEDATA is not None:
                with open(FAKEDATA, "r", encoding="utf-8") as datei:
                    response = json_loads(datei.read())
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

    def __get_unit(self, key):
        """Function get unit from key Name."""
        if key.endswith(("pwr", "Pwr", "Power")):
            unit = "W"
        elif key.endswith(("amp", "Amp")):
            unit = "A"
        elif key.endswith(("soc", "Soc", "soh", "Soh")):
            unit = "%"
        elif key.endswith(("vol", "Vol")):
            unit = "V"
        elif key.endswith(("Watth", "Energy")):
            unit = "Wh"
        elif "Generation" in key:
            unit = "kWh"
        elif key.startswith("bpTemp"):  # TODO: alternative: 'Temp' in key
            unit = "°C"
        else:
            unit = None

        return unit

    def __get_description(self, key):
        # TODO: hier könnte man noch mehr definieren bzw ein translation dict erstellen +1
        # Comment: Ich glaube hier brauchen wir n
        description = key  # default description
        if key == "sysLoadPwr":
            description = "Hausnetz"
        if key == "sysGridPwr":
            description = "Stromnetz"
        if key == "mpptPwr":
            description = "Solarertrag"
        if key == "bpPwr":
            description = "Batterieleistung"
        if key == "bpSoc":
            description = "Ladezustand der Batterie"
        if key == "online":
            description = "Online"
        if key == "systemName":
            description = "System Name"
        if key == "createTime":
            description = "Installations Datum"
        # Battery descriptions
        if key == "bpVol":
            description = "Batteriespannung"
        if key == "bpAmp":
            description = "Batteriestrom"
        if key == "bpCycles":
            description = "Ladezyklen"
        if key == "bpTemp":
            description = "Temperatur der Batteriezellen"

        return description

    def _get_sensors(self, response):
        # get sensors from response['data']
        sensors = self.__get_sensors_data(response)

        # get sensors from 'JTS1_ENERGY_STREAM_REPORT'
        # sensors = self.__get_sensors_energy_stream(response, sensors)  # is currently not in use

        # get sensors from 'JTS1_EMS_CHANGE_REPORT'
        # siehe parameter_selected.json    #  get bpSoc from ems_change
        sensors = self.__get_sensors_ems_change(response, sensors)

        # get info from batteries  => JTS1_BP_STA_REPORT
        sensors = self.__get_sensors_battery(response, sensors)

        # get info from PV strings  => JTS1_EMS_HEARTBEAT
        sensors = self.__get_sensors_ems_heartbeat(response, sensors)

        return sensors

    def __get_sensors_data(self, response):
        d = response["data"].copy()

        # sensors not in use: note, bpSoc is taken from the EMS CHANGE report
        # [ 'bpSoc', 'sysBatChgUpLimit', 'sysBatDsgDownLimit','sysGridSta', 'sysOnOffMachineStat',
        #   'location', 'timezone', 'quota']

        sens_select = [
            "sysLoadPwr",
            "sysGridPwr",
            "mpptPwr",
            "bpPwr",
            "online",
            "todayElectricityGeneration",
            "monthElectricityGeneration",
            "yearElectricityGeneration",
            "totalElectricityGeneration",
            "systemName",
            "createTime",
        ]

        sensors = dict()  # start with empty dict
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                if not isinstance(value, dict):
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{key}"
                    special_icon = None
                    if key == "mpptPwr":
                        special_icon = "mdi:solar-power"

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

    # Note, this report is currently not in use. Sensors are taken from response['data']
    # def __get_sensors_energy_stream(self, response, sensors):
    #     report = "JTS1_ENERGY_STREAM_REPORT"
    #     d = response["data"]["quota"][report]
    #     prefix = (
    #         "_".join(report.split("_")[1:3]).lower() + "_"
    #     )  # used to construct sensor name
    #
    #     # sens_all = ['bpSoc', 'mpptPwr', 'updateTime', 'bpPwr', 'sysLoadPwr', 'sysGridPwr']
    #     sens_select = d.keys()
    #     data = {}
    #     for key, value in d.items():
    #         if key in sens_select:  # use only sensors in sens_select
    #             # default uid, unit and descript
    #             unique_id = f"{self.sn}_{report}_{key}"
    #
    #             data[unique_id] = PowerOceanEndPoint(
    #                 internal_unique_id=unique_id,
    #                 serial=self.sn,
    #                 name=f"{self.sn}_{prefix+key}",
    #                 friendly_name=prefix + key,
    #                 value=value,
    #                 unit=self.__get_unit(key),
    #                 description=self.__get_description(key),
    #                 icon=None,
    #             )
    #     dict.update(sensors, data)
    #
    #     return sensors

    def __get_sensors_ems_change(self, response, sensors):
        report = "JTS1_EMS_CHANGE_REPORT"
        d = response["data"]["quota"][report]

        sens_select = [
            "bpTotalChgEnergy",
            "bpTotalDsgEnergy",
            "bpSoc",
            "bpOnlineSum",  # number of batteries
            "emsCtrlLedBright",
        ]

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

    def __get_sensors_battery(self, response, sensors):
        report = "JTS1_BP_STA_REPORT"
        d = response["data"]["quota"][report]
        keys = list(d.keys())

        # loop over N batteries:
        batts = [s for s in keys if len(s) > 12]
        bat_sens_select = [
            "bpPwr",
            "bpSoc",
            "bpSoh",
            "bpVol",
            "bpAmp",
            "bpCycles",
            "bpSysState",
            "bpRemainWatth",
        ]

        data = {}
        prefix = "bpack"
        for ibat, bat in enumerate(batts):
            name = prefix + "%i_" % (ibat + 1)
            d_bat = json_loads(d[bat])
            for key, value in d_bat.items():
                if key in bat_sens_select:
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{report}_{bat}_{key}"
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
            unique_id = f"{self.sn}_{report}_{bat}_{key}"
            description_tmp = f"{name}" + self.__get_description(key)
            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{name + key}",
                friendly_name=name + key,
                value=value,
                unit=self.__get_unit(key),
                description=description_tmp,
                icon=None,
            )

        dict.update(sensors, data)

        return sensors

    def __get_sensors_ems_heartbeat(self, response, sensors):
        report = "JTS1_EMS_HEARTBEAT"
        d = response["data"]["quota"][report]
        # sens_select = d.keys()  # 68 Felder
        sens_select = [
            "bpRemainWatth",
            "emsBpAliveNum",
            "emsBpPower",
            "pcsActPwr",
            "pcsMeterPower",
        ]
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
        n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])  # TODO: auch als Sensor?
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

        return sensors


class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
