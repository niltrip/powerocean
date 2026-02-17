"""
ecoflow.py: Async EcoFlow API client for PowerOcean integration.

This module provides the `EcoflowApi` class for authenticating with the
EcoFlow cloud service, fetching device data, and validating responses.

It also defines several exceptions for error handling:
- `ApiResponseError` for generic API response issues.
- `ResponseTypeError` for invalid response types.
- `AuthenticationFailedError` for login failures.
- `FailedToExtractKeyError` when a required key is missing in a response.

Usage:
    api = EcoflowApi(hass, serialnumber, username, password, variant)
    await api.async_authorize()
    data = await api.fetch_raw()
"""

import asyncio
import base64
import ssl
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, IntegrationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import uuid
from homeassistant.util.json import json_loads
from paho.mqtt.client import Client as MQTTClient
from paho.mqtt.client import MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import Json

from .const import (
    BASE_URI,
    ISSUE_URL_ERROR_MESSAGE,
    LOGGER,
    MOCKED_RESPONSE,
    USE_MOCKED_RESPONSE,
)
from .types import EcoflowMqttInfo


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
        self.token: str | None = None
        self.device: dict | None = None
        self.url_authorize = f"{BASE_URI}/auth/login"
        self.url_authorize_mqtt = f"{BASE_URI}/iot-auth/app/certification"
        self.url_fetch_data = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        self.hass = hass
        self.user_id = None
        self._mqtt_client = None
        self._mqtt_connected = False

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

        session = async_get_clientsession(self.hass)

        # Hilfsfunktion für konsistentes Reraising
        def _raise(exc: Exception) -> None:
            raise exc

        try:
            async with asyncio.timeout(10):
                response = await session.post(url, json=data, headers=headers)
                response.raise_for_status()
                response_data = await response.json()

            data_block = response_data.get("data")
            self.user_id = response_data["data"]["user"]["userId"]
            if not isinstance(data_block, dict) or "token" not in data_block:
                msg = "Missing or malformed 'data' block in response"
                LOGGER.error(msg)
                _raise(AuthenticationFailedError(msg))

            self.token = data_block["token"]

        except AuthenticationFailedError as auth_err:
            LOGGER.warning(
                "Authentication failed for user %s: %s",
                self.ecoflow_username,
                auth_err,
            )
            msg = "Invalid username or password"
            raise IntegrationError(msg) from auth_err

        except (TimeoutError, aiohttp.ClientError) as conn_err:
            LOGGER.warning("Cannot connect to EcoFlow API at %s: %s", url, conn_err)
            # Netzwerkprobleme → HA sollte retryen
            msg = "Cannot connect to EcoFlow API at %s"
            raise ConfigEntryNotReady(msg, url) from conn_err

        except Exception as unexpected:
            LOGGER.exception("Unexpected error during EcoFlow login")
            msg = "Unexpected error during login"
            raise IntegrationError(msg) from unexpected

        else:
            LOGGER.info("Successfully logged in.")
            await self._async_setup_mqtt()
            return True

    async def fetch_raw(self) -> dict[str, Any]:
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

    def _validate_response(self, response: dict[str, Any]) -> dict:
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

    def _accept_mqqt_certification(self, resp_json: dict) -> None:
        LOGGER.debug(f"Received MQTT credentials: {resp_json}")
        try:
            mqtt_url = resp_json["data"]["url"]
            mqtt_port = int(resp_json["data"]["port"])
            mqtt_username = resp_json["data"]["certificateAccount"]
            mqtt_password = resp_json["data"]["certificatePassword"]
            self.mqtt_info = EcoflowMqttInfo(
                mqtt_url, mqtt_port, mqtt_username, mqtt_password
            )
        except KeyError as key:
            msg = "Failed to extract key %s from %s", key, resp_json
            raise EcoflowException(msg)

        LOGGER.debug(f"Successfully extracted account: {self.mqtt_info.username}")

    async def _async_setup_mqtt(self) -> None:
        """Set up an MQTT client."""
        session = async_get_clientsession(self.hass)
        headers = {
            "lang": "en_US",
            "authorization": f"Bearer {self.token}",
            "product-type": self.ecoflow_variant,
            "content-type": "application/json",
        }
        user_data = {"userId": self.user_id}
        req_params = {}
        response = await session.get(
            self.url_authorize_mqtt,
            data=user_data,
            params=req_params,
            headers=headers,
        )
        data = await response.json()
        self._accept_mqqt_certification(data)
        self.mqtt_info.client_id = (
            f"ANDROID_{str(uuid.random_uuid_hex()).upper()}_{self.user_id}"
        )
        if self._mqtt_client is not None:
            return

        # Client erzeugen
        self._mqtt_client = MQTTClient(
            client_id=self.mqtt_info.client_id,
            clean_session=True,
            reconnect_on_failure=True,
            callback_api_version=CallbackAPIVersion.VERSION2,
        )

        # Auth setzen
        self._mqtt_client.username_pw_set(
            self.mqtt_info.username,
            self.mqtt_info.password,
        )
        client = self._mqtt_client

        # TLS Setup (BLOCKING → Executor)
        def _setup_tls() -> None:
            context = ssl.create_default_context()
            client.tls_set_context(context)
            client.tls_insecure_set(False)

        await self.hass.async_add_executor_job(_setup_tls)

        # Callbacks setzen
        client.on_connect = self._on_mqtt_connect
        client.on_disconnect = self._on_mqtt_disconnect
        client.on_message = self._on_mqtt_message

        # Connect (BLOCKING → Executor)
        await self.hass.async_add_executor_job(
            client.connect,
            self.mqtt_info.url,
            self.mqtt_info.port,
            60,
        )

        # MQTT Thread starten
        await self.hass.async_add_executor_job(client.loop_start)

    def _on_mqtt_connect(
        self, client: MQTTClient, userdata, flags, rc, properties=None
    ) -> None:
        if rc == 0:
            LOGGER.info("MQTT connected")
            self._mqtt_connected = True

            client.subscribe(f"/app/device/property/{self.sn}", qos=0)

            LOGGER.info("Subscribed to MQTT topics")
        else:
            LOGGER.error("MQTT connect failed: %s", rc)

    def _on_mqtt_disconnect(
        self, client: MQTTClient, userdata, rc, properties=None
    ) -> None:
        LOGGER.warning("MQTT disconnected: %s", rc)
        self._mqtt_connected = False

    def _on_mqtt_message(self, client: MQTTClient, userdata, message) -> None:
        # MQTT liefert binäre Daten → ignorieren
        pass


class EcoflowException(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)


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
