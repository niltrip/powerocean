"""resolapi.py: Resol API for Resol integration."""

import requests
import re
import datetime

from collections import namedtuple
from homeassistant.exceptions import IntegrationError
from requests.exceptions import RequestException, Timeout

from .const import (
    _LOGGER,
    ISSUE_URL_ERROR_MESSAGE
)


# Better storage of Resol endpoint
ResolEndPoint = namedtuple('ResolEndPoint', 'internal_unique_id, serial, name, friendly_name, value, unit, description, destination, source')

# ResolAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class ResolAPI:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.device = None
        self.session = requests.Session()

    def detect_device(self):

        try:
            url = f"http://{self.host}:{self.port}/cgi-bin/get_resol_device_information"
            response = requests.request("GET", url, timeout=5)

            _LOGGER.debug(f"Attempting to discover Resol device via get: {url}")
            if(response.status_code == 200):
                matches = re.search(r'product\s=\s["](.*?)["]', response.text)
                if matches:
                    self.device = {
                        'product': matches.group(1),
                        'vendor': re.search(r'vendor\s=\s["](.*?)["]', response.text).group(1),
                        'serial': re.search(r'serial\s=\s["](.*?)["]', response.text).group(1),
                        'version': re.search(r'version\s=\s["](.*?)["]', response.text).group(1),
                        'build': re.search(r'build\s=\s["](.*?)["]', response.text).group(1),
                        'name': re.search(r'name\s=\s["](.*?)["]', response.text).group(1),
                        'features': re.search(r'features\s=\s["](.*?)["]', response.text).group(1),
                        'host': self.host,
                        'port': self.port,
                        'mac': self.format_serial_to_mac(re.search(r'serial\s=\s["](.*?)["]', response.text).group(1)) #convert serial to MAC address
                    }
                    _LOGGER.debug(f"{self.device['serial']}: Resol device data received: {self.device}")
                else:
                    error = f"{self.device['serial']}: Your device was reachable at {url } but could not be successfully detected."
                    _LOGGER.warning(error+ISSUE_URL_ERROR_MESSAGE)
                    raise IntegrationError(error)
            else:
                error = f"Are you sure you entered the correct address {url} of the Resol KM2 device?"
                _LOGGER.warning(error+ISSUE_URL_ERROR_MESSAGE)
                raise IntegrationError(error)

        except ConnectionError:
            _LOGGER.warning(f"Unable to connect to {self.host}. Device might be offline."+ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

        except RequestException as e:
            error = f"Error detecting Resol device - {e}"
            _LOGGER.error(f"Error detecting Resol device - {e}"+ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

        return self.device


    # Fetch the data from the Resol KM2 device, which then constitues the Sensors
    def fetch_data_km2(self):
        response = {}

        url = f"http://{self.host}:{self.port}/cgi-bin/resol-webservice"
        _LOGGER.debug(f"{self.device['serial']}: KM2 requesting sensor data from url {url}")

        try:
            headers = {
                'Content-Type': 'application/json'
            }

            payload = "[{'id': '1','jsonrpc': '2.0','method': 'login','params': {'username': '" + self.username + "','password': '" + self.password + "'}}]"
            response = requests.request("POST", url, headers=headers, data = payload)
            response_auth = response.json()[0]

            # Check if correct login credentials

            # BAD RESPONSE: [{'jsonrpc': '2.0', 'id': '1', 'error': {'category': 'App', 'message': 'Invalid credentials'}}]
            if 'error' in response_auth:
                error_message = response_auth['error'].get('message')
                _LOGGER.warning(f"{self.device['serial']}: Authentication failed: {error_message}")
                raise AuthenticationFailed(error_message)

            # GOOD RESPONSE: [{'jsonrpc': '2.0', 'id': '1', 'result': {'authId': 'a463cc3ab307c7ef7d85f22daf15f0'}}]
            elif 'result' in response_auth and 'authId' in response_auth['result']:
                authId = response_auth['result']['authId']
                _LOGGER.debug(f"{self.device['serial']}: Successfully authenticated. Auth ID: {authId}")

                # Authenticate and get the actual sensors response data
                payload = "[{'id': '1','jsonrpc': '2.0','method': 'dataGetCurrentData','params': {'authId': '" + authId + "'}}]"
                response = requests.request("POST", url, headers=headers, data = payload)
                response_data = response.json()[0]["result"]
                _LOGGER.debug(f"{self.device['serial']}: KM2 response: {response_data}")

                # Proceed to parsing
                return self.__parse_data(response_data)


        except KeyError:
            error = f"{self.device['serial']}: Please re-check your username and password in your configuration!"
            _LOGGER.warning(error+ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

        except ConnectionError:
            error = f"ConnectionError in fetch_data_km2(): Unable to connect to {self.host}. Device might be offline."
            _LOGGER.warning(error+ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None

        except RequestException as e:
            error = f"RequestException in fetch_data_km2(): Error while fetching data from {self.host}: {e}"
            _LOGGER.warning(error+ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)
            return None





    def __parse_data(self, response):
        # Implement the logic to parse the response from the Resol device

        data = {}

        iHeader = 0
        for header in response["headers"]:
            _LOGGER.debug(f"{self.device['serial']}: Found header[{iHeader}] now parsing it ...")
            iField = 0
            for field in response["headers"][iHeader]["fields"]:
                value = response["headersets"][0]["packets"][iHeader]["field_values"][iField]["raw_value"]
                if isinstance(value, float):
                    value = round(value, 2)
                if "date" in field["name"]:
                    epochStart = datetime.datetime(2001, 1, 1, 0, 0, 0, 0)
                    value = epochStart + datetime.timedelta(0, value)

                # Sensor's unique ID combination of device serial and each header/field unique name as internal sensor hash (not the entity_id)
                unique_id = self.device['serial'] + "_" + header["id"] + "__" + field["id"]
                data[unique_id] = ResolEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.device['serial'],
                    name=self.device['serial'].lower() + "_" + field["name"].replace(" ", "_").lower(),
                    friendly_name=field["name"].replace(" ", "_").lower(),
                    value=value,
                    unit=field["unit"].strip(),
                    description=header["description"],
                    destination=header["destination_name"],
                    source=header["source_name"]
                    )
                iField += 1
            iHeader +=1

        return data


    def format_serial_to_mac(self, serial: str) -> str:
        # Split the serial into chunks of two characters
        mac_chunks = [serial[i:i+2] for i in range(0, len(serial), 2)]
        # Join the chunks with colons
        mac_address = ':'.join(mac_chunks)
        return mac_address

class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""