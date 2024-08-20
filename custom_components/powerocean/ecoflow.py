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
        self.authorize()  # authorize user and get device details

    def get_device(self):
        """Function get device"""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.15",      # TODO: woher bekommt man diese Info? Hab es aus der App
            "build": "13",            # TODO: wo finde ich das?
            "name": "PowerOcean",
            "features": "Photovoltaik"
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
            "userType": "ECOFLOW"
        }

        try:
            url = self.url_iot_app
            _LOGGER.info("Login to EcoFlow API %s", {url})
            request = requests.post(url, json=data, headers=headers)
            response = self.get_json_response(request)

        except ConnectionError:
            error = f"Unable to connect to {self.url_iot_app}. Device might be offline."
            _LOGGER.warning( error + ISSUE_URL_ERROR_MESSAGE )
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
            raise Exception(f"Got HTTP status code {request.status_code}: {request.text}")
        try:
            response = json_loads(request.text)
            response_message = response["message"]
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from {json_loads(request.text)}")
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
        if key.endswith("pwr") or key.endswith("Pwr"):
            unit = "W"
        elif key.endswith("amp") or key.endswith("Amp"):
            unit = "A"
        elif key.endswith("soc") or key.endswith("Soc"):
            unit = "%"
        elif key.endswith("soh") or key.endswith("Soh"):
            unit = "%"
        elif key == "vol":
            unit = "V"
        elif "Energy" in key:
            unit = "Wh"
        elif "Generation" in key:
            unit = "kWh"
        else:
            unit = ""

        return unit

    def __get_description(self, key):

        # TODO: hier könnte man noch mehr definieren bzw ein translation dict erstellen
        description = key   # default description
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
            description_tmp = "Batteriespannung"
        if key == "bpAmp":
            description_tmp = "Batteriestrom"
        if key == "bpCycles":
            description_tmp = "Ladezyklen"

        return description


    def _get_sensors(self, response):

        #  TODO - Comment: Note, unique_id will change!! Compatibility broken!!!
        #        => ist nur wichtig bei Langzeitdatenerfassung (z.B. influx)

        # get sensors from response['data']
        # TODO - Question:  for final version
        #  - alle Daten aus 'JTS1_EMS_CHANGE_REPORT' gibt es hier auch.
        #  - brauchen wir die beide?
        sensors = self.__get_sensors_data(response)

        # get sensors from 'JTS1_ENERGY_STREAM_REPORT'
        sensors = self.__get_sensors_energy_stream(response, sensors)

        # get sensors from 'JTS1_EMS_CHANGE_REPORT'
        # TODO - QUESTION:  hier würde ich (in der finalen Version) deutlich weniger einlesen bzw. enablen
        #  In meinem System hatte ich alles Sensoren ca. 4 Monate lang beobachtet. Nur ganz wenige zeigen Änderungen.
        #  Vieles davon sagt mir auch nichts. Mein Vorschlag für die finale Version wäre:
        #  ein Liste von interessanten Sensoren zu definieren und die anderen auf "disable" zu setzen
        sensors = self.__get_sensors_ems_change(response, sensors)

        # get info from batteries  => JTS1_BP_STA_REPORT
        sensors = self.__get_sensors_battery(response, sensors)


        # get info from PV strings  => JTS1_EMS_HEARTBEAT
        sensors = self.__get_sensors_pvstrings(response, sensors)

        return sensors

    def __get_sensors_data(self, response):
        d = response["data"].copy()
        r = d.pop('quota')  # remove quota dict

        # sens_all = ['sysLoadPwr', 'sysGridPwr', 'mpptPwr', 'bpPwr', 'bpSoc', 'sysBatChgUpLimit',
        #             'sysBatDsgDownLimit','sysGridSta', 'sysOnOffMachineStat', 'online',
        #             'todayElectricityGeneration', 'monthElectricityGeneration', 'yearElectricityGeneration',
        #             'totalElectricityGeneration','systemName', 'createTime', 'location', 'timezone', 'quota']

        # TODO: this would be my suggestion for the selection of sensors
        # sens_select = ['sysLoadPwr', 'sysGridPwr', 'mpptPwr', 'bpPwr', 'bpSoc', 'online',
        #                'todayElectricityGeneration', 'monthElectricityGeneration',
        #                'yearElectricityGeneration', 'totalElectricityGeneration',
        #                'systemName', 'createTime']

        sens_select = d.keys()  # TODO: in case you want to read in all sensors

        sensors = dict()  # start with empty dict
        for key, value in d.items():
            if key in sens_select:   # use only sensors in sens_select
                if not isinstance(value, dict):
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{key}"
                    unit_tmp = self.__get_unit(key)
                    description_tmp = self.__get_description(key)
                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=unit_tmp,
                        description=description_tmp
                    )

        return sensors

    def __get_sensors_energy_stream(self, response, sensors):
        report = "JTS1_ENERGY_STREAM_REPORT"
        d = response["data"]["quota"][report]

        # TODO: note, the prefix name is introduced to avoid doubling the sensor names
        prefix = '_'.join(report.split('_')[1:3]).lower() + '_' # used to construct sensor name

        # sens_all = ['bpSoc', 'mpptPwr', 'updateTime', 'bpPwr', 'sysLoadPwr', 'sysGridPwr']
        sens_select = d.keys()
        data = {}
        for key, value in d.items():
            if key in sens_select:   # use only sensors in sens_select
                # default uid, unit and descript
                unique_id = f"{self.sn}_{report}_{key}"
                unit_tmp = self.__get_unit(key)
                description_tmp = self.__get_description(key)
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{prefix+key}",
                    friendly_name=prefix+key,
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )
        dict.update(sensors, data)

        return sensors

    def __get_sensors_ems_change(self, response, sensors):
        report = "JTS1_EMS_CHANGE_REPORT"
        d = response["data"]["quota"][report]
        prefix = '_'.join(report.split('_')[1:3]).lower() + '_' # used to construct sensor name

        # sens_select = ['bpTotalChgEnergy', 'bpTotalDsgEnergy', 'bpSoc', 'bpOnlineSum', 'emsCtrlLedBright'
        #                # TODO: we need a loop over strings if we want to use the sensors below
        #                'mppt1WarningCode', 'mppt2WarningCode', 'mppt1FaultCode', 'mppt2FaultCode'
        #                ]

        # TODO: here we get more than 200 sensors
        sens_select = d.keys()
        data = {}
        for key, value in d.items():
            if key in sens_select:   # use only sensors in sens_select
                # Exceptions empty or special structure
                if key == "evBindList" or key == "emsSgReady":
                    continue
                # default uid, unit and descript
                unique_id = f"{self.sn}_{report}_{key}"
                unit_tmp = self.__get_unit(key)
                description_tmp = self.__get_description(key)  # TODO: update description

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{prefix+key}",
                    friendly_name=prefix+key,
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )
        dict.update(sensors, data)

        return sensors

    def __get_sensors_battery(self, response, sensors):

        report = "JTS1_BP_STA_REPORT"
        d = response["data"]["quota"][report]
        removed = d.pop('')  # first element is empty
        keys = list(d.keys())
        n_bat = len(keys) - 1  # number of batteries found

        sens_select = d.keys()
        data = {}
        prefix = 'bp_'

        # collect updateTime
        key = 'updateTime'
        name = prefix + key
        value = d[key]
        unique_id = f"{self.sn}_{name}"
        data[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=self.sn,
            name=f"{self.sn}_{name}",
            friendly_name= name,
            value=value,
            unit="",
            description="Letzte Aktualisierung"
        )

        # create sensor for number of batteries
        key = 'number_of_batteries'
        name = prefix + key
        unique_id = f"{self.sn}_{name}"
        unit_tmp = ""
        description_tmp = "Anzahl der Batterien"
        data[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=self.sn,
            name=f"{self.sn}_{name}",
            friendly_name = name,
            value=n_bat,
            unit=unit_tmp,
            description=description_tmp,
        )


        # loop over N batteries:
        # TODO: we may want to compute the mean cell temperature

        batts = keys[1:]
        ibat = 0
        bat_sens_select = ['bpPwr', 'bpSoc', 'bpSoh', 'bpVol', 'bpAmp','bpCycles', 'bpSysState']
        for ibat, bat in enumerate(batts):
            prefix = prefix + 'bat%i_' % (ibat+1)
            d_bat = json_loads(d[bat])
            for key, value in d_bat.items():
                if key in bat_sens_select:
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{bat}_{key}"
                    unit_tmp = self.__get_unit(key)
                    description_tmp = f"{prefix}" + self.__get_description(key)
                    data[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{prefix + key}",
                        friendly_name=prefix+key,
                        value=value,
                        unit=unit_tmp,
                        description=description_tmp,
                    )

        dict.update(sensors, data)

        return sensors

    def __get_sensors_pvstrings(self, response, sensors):
        # TODO - Question: wie kann ich d um einen Schritt für phase erweitern?
        #                 => Ich muss gestehen, dass ich die Frage nicht verstanden habe
        # TODO - Comment: hab die Funktion nahezu so gelassen wie Du sie gemacht hast

        report = "JTS1_EMS_HEARTBEAT"
        d = response["data"]["quota"][report]
        sens_select = d.keys()  # 68 Felder
        data = {}
        prefix = 'pv_'
        for key, value in d.items():
            # Exceptions empty or special structure
            if (
                # key == "pcsAPhase"
                # or key == "pcsBPhase"
                # or key == "pcsCPhase"
                # or key == "mpptHeartBeat"
                isinstance(value, dict)     # exclude ["pcsAPhase", "pcsBPhase","pcsCPhase", "mpptHeartBeat"]
                or isinstance(value, list)  # exclude meterData or write extra routine
            ):
                continue

            name = prefix + key
            unique_id = f"{self.sn}_{report}_{name}"
            unit_tmp = self.__get_unit(key)
            description_tmp = self.__get_description(key)
            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{name}",
                friendly_name=name,
                value=value,
                unit=unit_tmp,
                description=description_tmp,
            )
        # special for phases
        report = "JTS1_EMS_HEARTBEAT"
        phases=["pcsAPhase", "pcsBPhase","pcsCPhase"]
        for i, phase in enumerate(phases):
            for key, value in d[phase].items():
                name = prefix + phase + '_' +  key
                unique_id = f"{self.sn}_{report}_{name}"
                unit_tmp = self.__get_unit(key)
                description_tmp = self.__get_description(key)

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{name}",
                    friendly_name=f"{name}",
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )

        # special for mpptPv
        report = "JTS1_EMS_HEARTBEAT"
        mpptpvs=["mpptPv1", "mpptPv2"]
        mpptPv_sum = 0.0
        for i, mpptpv in enumerate(mpptpvs):
            for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                unique_id = f"{self.sn}_{report}_mpptHeartBeat_{mpptpv}_{key}"
                unit_tmp = self.__get_unit(key)
                description_tmp = self.__get_description(key)
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{mpptpv}_{key}",
                    friendly_name=f"{mpptpv}_{key}",
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )
                # sum power of all strings
                if key == 'pwr':
                    mpptPv_sum += value

        # create total power sensor of all strings
        name = "mpptPv_pwrTotal"
        unique_id = f"{self.sn}_{report}_mpptHeartBeat_{name}"
        unit_tmp = self.__get_unit('pwr')
        description_tmp = "Solarertrag aller Strings"
        data[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=self.sn,
            name=f"{self.sn}_{name}",
            friendly_name=f"{name}",
            value=mpptPv_sum,
            unit=unit_tmp,
            description=description_tmp,
        )

        dict.update(sensors, data)

        return sensors

class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
