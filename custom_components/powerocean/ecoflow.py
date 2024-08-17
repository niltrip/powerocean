"""ecoflow.py: API for PowerOcean integration."""

import requests
import datetime
import base64
import json
import re

from collections import namedtuple
from homeassistant.exceptions import IntegrationError
from requests.exceptions import RequestException, Timeout

from .const import _LOGGER, ISSUE_URL_ERROR_MESSAGE


# Better storage of PowerOcean endpoint
PowerOceanEndPoint = namedtuple(
    "PowerOceanEndPoint",
    "internal_unique_id, serial, name, friendly_name, value, unit, description",
)


# ecoflow_api to detect device and get device info, fetch the actual data from the PowerOcean device, and parse it
class ecoflow_api:
    """Class representing ecoflow_api"""
    def __init__(self, serialnumber, username, password):
        self.username = username
        self.password = password
        self.sn = serialnumber
        self.token = ""
        self.device = None
        self.session = requests.Session()

    def detect_device(self):
        try:
            # curl 'https://api-e.ecoflow.com/auth/login' \
            # -H 'content-type: application/json' \
            # --data-raw '{"userType":"ECOFLOW","scene":"EP_ADMIN","email":"","password":""}'

            url = f"https://api-e.ecoflow.com/auth/login"
            headers = {"lang": "en_US", "content-type": "application/json"}
            data = {
                "email": self.username,
                "password": base64.b64encode(self.password.encode()).decode(),
                "scene": "IOT_APP",
                "userType": "ECOFLOW",
            }

            _LOGGER.debug(f"Login to EcoFlow API {url}")
            request = requests.post(url, json=data, headers=headers, timeout=30)
            response = self.get_json_response(request)
            _LOGGER.debug(f"{response}")

            try:
                self.token = response["data"]["token"]
            except KeyError as key:
                raise Exception(
                    f"Failed to extract key {key} from response: {response}"
                )

            self.device = {
                "product": "PowerOcean",
                "vendor": "Ecoflow",
                "serial": self.sn,
                "version": "5.1.15",
                "build": "6",
                "name": "PowerOcean",
                "features": "Photovoltaik",
            }

        except ConnectionError:
            _LOGGER.warning(
                f"Unable to connect to {url}. Device might be offline."
                + ISSUE_URL_ERROR_MESSAGE
            )
            raise IntegrationError(error)
            return None

        except RequestException as e:
            error = f"Error detecting PowerOcean device - {e}"
            _LOGGER.error(
                f"Error detecting PowerOcean device - {e}" + ISSUE_URL_ERROR_MESSAGE
            )
            raise IntegrationError(error)
            return None

        return self.device

    def get_json_response(self, request):
        """Function printing python version."""
        if request.status_code != 200:
            raise Exception(
                f"Got HTTP status code {request.status_code}: {request.text}"
            )

        try:
            response = json.loads(request.text)
            response_message = response["message"]
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from {response}") from key
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

        url = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"

        try:
            headers = {"authorization": f"Bearer {self.token}"}

            request = requests.get(url, headers=headers, timeout=30)
            response = self.get_json_response(request)

            _LOGGER.debug(f"{response}")

            # Proceed to parsing
            return self.__parse_data(response)

        except ConnectionError:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

        except RequestException as e:
            error = f"RequestException in fetch_data: Error while fetching data from {url}: {e}"
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

    def get_unit(self, key):
        """Function get unit from keyName."""
        if key.endswith("pwr") or key.endswith("Pwr"):
            unit = "W"
        elif key.endswith("amp") or key.endswith("Amp"):
            unit = "A"
        elif key == "bpSoc":
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

    def __parse_data(self, response):
        # Implement the logic to parse the response from the PowerOcean device

        data = {}

        #        json_data = json.load(f"{response}")
        #        _LOGGER.info(f"{json_data}")
        #        _LOGGER.info(json_data["data"]["quota"]["JTS1_EMS_HEARTBEAT"])

        for key, value in response["data"].items():
            if key == "quota":
                continue
            unique_id = f"{self.sn}_{key}"
            unit_tmp = self.get_unit(key)
            description_tmp = {key}
            if key == "sysLoadPwr":
                description_tmp = "Hausnetz"
            if key == "sysGridPwr":
                description_tmp = "Stromnetz"
            if key == "mpptPwr":
                description_tmp = "Solarertrag"
            if key == "bpPwr":
                description_tmp = "Batterieleistung"
            if key == "bpSoc":
                description_tmp = "Ladezustand der Batterie"

            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{key}",
                friendly_name=key,
                value=value,
                unit=unit_tmp,
                description=description_tmp,
            )

        report = "JTS1_ENERGY_STREAM_REPORT"
        for key, value in response["data"]["quota"][report].items():
            unique_id = f"{self.sn}_{report}_{key}"
            unit_tmp = self.get_unit(key)
            description_tmp = {key}
            if key == "sysLoadPwr":
                description_tmp = "Hausnetz"
            if key == "sysGridPwr":
                description_tmp = "Stromnetz"
            if key == "mpptPwr":
                description_tmp = "Solarertrag"
            if key == "bpPwr":
                description_tmp = "Batterieleistung"
            if key == "bpSoc":
                description_tmp = "Ladezustand der Batterie"

            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{key}",
                friendly_name=key,
                value=value,
                unit=unit_tmp,
                description=description_tmp,
            )

        report = "JTS1_EMS_CHANGE_REPORT"
        for key, value in response["data"]["quota"][report].items():
            # Exceptions empty or special structure
            if key == "evBindList" or key == "emsSgReady":
                continue
            unique_id = f"{self.sn}_{report}_{key}"
            unit_tmp = self.get_unit(key)
            description_tmp = key

            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{key}",
                friendly_name=key,
                value=value,
                unit=unit_tmp,
                description=description_tmp,
            )

        report = "JTS1_EMS_HEARTBEAT"
        for key, value in response["data"]["quota"][report].items():
            # Exceptions empty or special structure
            if (
                key == "pcsAPhase"
                or key == "pcsBPhase"
                or key == "pcsCPhase"
                or key == "mpptHeartBeat"
            ):
                continue
            unique_id = f"{self.sn}_{report}_{key}"
            unit_tmp = self.get_unit(key)
            description_tmp = key

            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=self.sn,
                name=f"{self.sn}_{key}",
                friendly_name=key,
                value=value,
                unit=unit_tmp,
                description=description_tmp,
            )
        # special for phases
        report = "JTS1_EMS_HEARTBEAT"
        phases=["pcsAPhase", "pcsBPhase","pcsCPhase"]
        for i, phase in enumerate(phases):
            for key, value in response["data"]["quota"][report][phase].items():
                unique_id = f"{self.sn}_{report}_{phase}_{key}"
                unit_tmp = self.get_unit(key)
                description_tmp = key

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{phase}_{key}",
                    friendly_name=f"{phase}_{key}",
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )

        # special for mpptPv
        report = "JTS1_EMS_HEARTBEAT"
        mpptpvs=["mpptPv1", "mpptPv2"]
        for i, mpptpv in enumerate(mpptpvs):
            for key, value in response["data"]["quota"][report]["mpptHeartBeat"][0]["mpptPv"][i].items():
                unique_id = f"{self.sn}_{report}_mpptHeartBeat_{mpptpv}_{key}"
                unit_tmp = self.get_unit(key)
                description_tmp = ""
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{self.sn}_{mpptpv}_{key}",
                    friendly_name=f"{mpptpv}_{key}",
                    value=value,
                    unit=unit_tmp,
                    description=description_tmp,
                )
        #next step
        #for key, value in response["data"]["quota"]["JTS1_BP_STA_REPORT"].items():
            #array füllen mit keys
            #Schleife über Array und daten ermitteln

        # for key, value in response["data"]["quota"]["JTS1_BP_STA_REPORT"][
        #     "HJ32ZDH4ZF8U0167"
        # ].items():
        #     unique_id = f"{self.sn}_bp1_{key}"
        #     unit_tmp = self.get_unit(key)
        #     description_tmp = ""
        #     data[unique_id] = PowerOceanEndPoint(
        #         internal_unique_id=unique_id,
        #         serial=self.sn,
        #         name=f"{self.sn}_bp1_{key}",
        #         friendly_name=f"bp1_{key}",
        #         value=value,
        #         unit=unit_tmp,
        #         description=description_tmp,
        #     )

        # for key, value in response["data"]["quota"]["JTS1_BP_STA_REPORT"][
        #     "HJ32ZDH4ZF8U0126"
        # ].items():
        #     unique_id = f"{self.sn}_bp2_{key}"
        #     unit_tmp = self.get_unit(key)
        #     description_tmp = ""
        #     data[unique_id] = PowerOceanEndPoint(
        #         internal_unique_id=unique_id,
        #         serial=self.sn,
        #         name=f"{self.sn}_bp2_{key}",
        #         friendly_name=f"bp2_{key}",
        #         value=value,
        #         unit=unit_tmp,
        #         description=description_tmp,
        #     )

        return data


class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
