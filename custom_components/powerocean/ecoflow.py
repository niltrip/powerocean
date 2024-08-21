"""ecoflow.py: API for PowerOcean integration."""

import requests
import base64
from collections import namedtuple
from requests.exceptions import RequestException

from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads

from .const import _LOGGER, ISSUE_URL_ERROR_MESSAGE


# Better storage of PowerOcean endpoint
PowerOceanEndPoint = namedtuple(
    "PowerOceanEndPoint",
    "internal_unique_id, serial, name, friendly_name, value, unit, description",
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
        # TODO: Unexpected Exception
        # self.authorize()  # authorize user and get device details

    def get_device(self):
        """Function get device"""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.15",  # TODO: woher bekommt man diese Info? Hab es aus der App
            "build": "13",  # TODO: wo finde ich das?
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
            headers = {"authorization": f"Bearer {self.token}"}
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)

            _LOGGER.debug(f"{response}")
            _LOGGER.debug(f"{response['data']}")

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
        if key.endswith(("pwr", "Pwr")):
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

        return description

    def _get_sensors(self, response):
        #  TODO - Comment: Note, unique_id will change!! Compatibility broken!!!
        #        => ist nur wichtig bei Langzeitdatenerfassung (z.B. influx) oder Energiedashboard

        # get sensors from response['data']
        # TODO - Question:  for final version
        #  - alle Daten aus 'JTS1_EMS_CHANGE_REPORT' gibt es hier auch.
        #  - brauchen wir die beide?
        #  Eigentlich würde ich Daten aus den expliziten "Reports" bevorzugen, dafür sind sie meines Erachtens vorgesehen.
        # Da es scheinbar keinen Unterschied hinsichtlich der Abfragezycklen gibt wäre ich für Kompatibilität und weglassen des "JTS1_ENERGY_STREAM_REPORT"
        sensors = self.__get_sensors_data(response)

        # get sensors from 'JTS1_ENERGY_STREAM_REPORT'
        # sensors = self.__get_sensors_energy_stream(response, sensors)

        # get sensors from 'JTS1_EMS_CHANGE_REPORT'
        # TODO - QUESTION:  hier würde ich (in der finalen Version) deutlich weniger einlesen bzw. enablen
        #  In meinem System hatte ich alles Sensoren ca. 4 Monate lang beobachtet. Nur ganz wenige zeigen Änderungen.
        #  Vieles davon sagt mir auch nichts. Mein Vorschlag für die finale Version wäre:
        #  ein Liste von interessanten Sensoren zu definieren und die anderen auf "disable" zu setzen
        # siehe parameter_selected.json
        sensors = self.__get_sensors_ems_change(response, sensors)

        # get info from batteries  => JTS1_BP_STA_REPORT
        sensors = self.__get_sensors_battery(response, sensors)

        # get info from PV strings  => JTS1_EMS_HEARTBEAT
        sensors = self.__get_sensors_ems_heartbeat(response, sensors)

        return sensors

    def __get_sensors_data(self, response):
        d = response["data"].copy()
        # TODO: nicht notwendig, wenn man sens_select verwendet
        r = d.pop("quota")  # remove quota dict

        # sens_all = ['sysLoadPwr', 'sysGridPwr', 'mpptPwr', 'bpPwr', 'bpSoc', 'sysBatChgUpLimit',
        #             'sysBatDsgDownLimit','sysGridSta', 'sysOnOffMachineStat', 'online',
        #             'todayElectricityGeneration', 'monthElectricityGeneration', 'yearElectricityGeneration',
        #             'totalElectricityGeneration','systemName', 'createTime', 'location', 'timezone', 'quota']

        # TODO: this would be my suggestion for the selection of sensors
        # delete bpSoc get from ems_change
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

        # sens_select = d.keys()  # TODO: in case you want to read in all sensors

        sensors = dict()  # start with empty dict
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
                    )

        return sensors

    # def __get_sensors_energy_stream(self, response, sensors):
    #     report = "JTS1_ENERGY_STREAM_REPORT"
    #     d = response["data"]["quota"][report]

    #     # TODO: note, the prefix name is introduced to avoid doubling the sensor names
    #     prefix = (
    #         "_".join(report.split("_")[1:3]).lower() + "_"
    #     )  # used to construct sensor name

    #     # sens_all = ['bpSoc', 'mpptPwr', 'updateTime', 'bpPwr', 'sysLoadPwr', 'sysGridPwr']
    #     sens_select = d.keys()
    #     data = {}
    #     for key, value in d.items():
    #         if key in sens_select:  # use only sensors in sens_select
    #             # default uid, unit and descript
    #             unique_id = f"{self.sn}_{report}_{key}"

    #             data[unique_id] = PowerOceanEndPoint(
    #                 internal_unique_id=unique_id,
    #                 serial=self.sn,
    #                 name=f"{self.sn}_{prefix+key}",
    #                 friendly_name=prefix + key,
    #                 value=value,
    #                 unit=self.__get_unit(key),
    #                 description=self.__get_description(key),
    #             )
    #     dict.update(sensors, data)

    #     return sensors

    def __get_sensors_ems_change(self, response, sensors):
        report = "JTS1_EMS_CHANGE_REPORT"
        d = response["data"]["quota"][report]
        prefix = (
            "_".join(report.split("_")[1:3]).lower() + "_"
        )  # used to construct sensor name

        sens_select = [
            "bpTotalChgEnergy",
            "bpTotalDsgEnergy",
            "bpSoc",
            "bpOnlineSum",
            "emsCtrlLedBright"
            # TODO: we need a loop over strings if we want to use the sensors below
            "mppt1WarningCode",
            "mppt2WarningCode",
            "mppt1FaultCode",
            "mppt2FaultCode",
        ]

        # TODO: here we get more than 200 sensors
        # sens_select = d.keys()
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
                )
        dict.update(sensors, data)

        return sensors

    def __get_sensors_battery(self, response, sensors):
        report = "JTS1_BP_STA_REPORT"
        d = response["data"]["quota"][report]
        # TODO: das klappt bei mir nicht. Bei mir ist das erste Element nicht leer!
        # Bei mir batts = Keylänge(Seriennummer) unter 12 Zeichen wird verworfen
        # batts = [s for s in keys if 12 <= len(s)]

        # removed = d.pop("")  # first element is empty
        keys = list(d.keys())
        n_bat = len(keys) - 2  # number of batteries found

        sens_select = d.keys()
        data = {}
        prefix = "bpack"

        # TODO: Ich denke die updateTime kann weg. Was ich sehe ist sie sogar noch in einer falschen Zeitzone
        # collect updateTime
        key = "updateTime"
        name = prefix + key
        value = d[key]
        unique_id = f"{self.sn}_{name}"
        data[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=self.sn,
            name=f"{self.sn}_{name}",
            friendly_name=name,
            value=value,
            unit=None,
            description="Letzte Aktualisierung",
        )

        # TODO: JTS1_EMS_CHANGE_REPORT Parameter bpOnlineSum = 2
        # Das zeigt 2 Batterien online, dein Parameter nicht notwendig
        # create sensor for number of batteries
        # key = "number_of_batteries"
        # name = prefix + key
        # unique_id = f"{self.sn}_{name}"
        # unit_tmp = ""
        # description_tmp = "Anzahl der Batterien"
        # data[unique_id] = PowerOceanEndPoint(
        #     internal_unique_id=unique_id,
        #     serial=self.sn,
        #     name=f"{self.sn}_{name}",
        #     friendly_name=name,
        #     value=n_bat,
        #     unit=unit_tmp,
        #     description=description_tmp,
        # )

        # loop over N batteries:
        # TODO: we may want to compute the mean cell temperature

        # batts = keys[1:]
        batts = [s for s in keys if len(s) > 12]
        ibat = 0
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
        for ibat, bat in enumerate(batts):
            name = prefix + "%i_" % (ibat + 1)
            d_bat = json_loads(d[bat])
            for key, value in d_bat.items():
                if key in bat_sens_select:
                    # default uid, unit and descript
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
        ]
        data = {}
        # prefix = "pv_"
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
                )

        # special for mpptPv
        mpptpvs = ["mpptPv1", "mpptPv2"]
        mpptPv_sum = 0.0
        for i, mpptpv in enumerate(mpptpvs):
            for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                unique_id = f"{self.sn}_{report}_mpptHeartBeat_{mpptpv}_{key}"

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{mpptpv}_{key}",
                    friendly_name=f"{mpptpv}_{key}",
                    value=value,
                    unit=self.__get_unit(key),
                    description=self.__get_description(key),
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
        )

        dict.update(sensors, data)

        return sensors


class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
