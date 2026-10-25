"""
Async EcoFlow PowerOcean API client.

This module provides a standalone, asyncio-based client for communicating
with the EcoFlow cloud API. It supports:

- User authentication
- Automatic region detection (EU/US)
- Fetching raw device data

The module is framework-independent and can be used both inside and
outside of Home Assistant.

Classes:
    EcoflowApi: Main API client class.
    EcoflowApiError: Base exception.
    EcoflowConnectionError: Raised on network errors and timeouts.
    AuthenticationError: Raised on login failure or rejected token.
    RegionDetectionError: Raised if API region cannot be detected.
"""

import asyncio
import base64
from http import HTTPStatus
from typing import Any, ClassVar

import aiohttp

from .const import LOGGER

HTTP_OK = 200
API_CODE_OK = "0"
LOGIN_URL = "https://api.ecoflow.com/auth/login"
# MQTT credentials of the account, as the EcoFlow app requests them
MQTT_CERTIFICATION_URL = "https://api.ecoflow.com/iot-auth/app/certification"
MQTT_CREDENTIAL_KEYS = ("url", "port", "certificateAccount", "certificatePassword")


class EcoflowApiError(Exception):
    """Base exception for all EcoFlow API errors."""


class EcoflowConnectionError(EcoflowApiError):
    """Raised when the EcoFlow cloud cannot be reached (network, timeout)."""


class AuthenticationError(EcoflowApiError):
    """Raised when authentication with the EcoFlow API fails."""


class RegionDetectionError(EcoflowApiError):
    """Raised when automatic API region detection fails."""


def _http_error(
    err: aiohttp.ClientResponseError, action: str, rejected: str
) -> EcoflowApiError:
    """Classify an HTTP error: rejected credentials, server or request error."""
    if err.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return AuthenticationError(rejected)
    msg = f"{action} failed with HTTP {err.status}"
    if err.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return EcoflowConnectionError(msg)
    return EcoflowApiError(msg)


def validate_response(response: Any) -> dict[str, Any]:
    """
    Validate the envelope of an EcoFlow API response.

    EcoFlow reports most errors with HTTP 200 and a non-zero ``code`` field,
    so the HTTP status alone is not enough.

    Raises:
        EcoflowApiError: If the response is not a successful data response.

    """
    if not isinstance(response, dict):
        msg = "API response is not a JSON object"
        raise EcoflowApiError(msg)

    code = response.get("code")
    if code is not None and str(code) != API_CODE_OK:
        msg = f"API returned error code {code}: {response.get('message')}"
        raise EcoflowApiError(msg)

    data = response.get("data")
    if data is None:
        msg = "API response missing required 'data' field"
        raise EcoflowApiError(msg)

    if not isinstance(data, dict):
        msg = "API response field 'data' is not an object"
        raise EcoflowApiError(msg)

    return response


class EcoflowApi:
    """
    Async client for the EcoFlow PowerOcean cloud API.

    This class handles:

    - Authentication against the EcoFlow cloud
    - Automatic region detection (EU / US endpoints)
    - Fetching raw device data

    The client can optionally reuse an externally provided
    aiohttp.ClientSession. If no session is provided, an internal
    session will be created automatically.

    This class is framework-agnostic and does not depend on
    Home Assistant.
    """

    REGION_HOSTS: ClassVar[dict[str, str]] = {
        "eu": "api-e.ecoflow.com",
        "us": "api-a.ecoflow.com",
    }

    def __init__(
        self,
        serialnumber: str,
        username: str,
        password: str,
        variant: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Initialize the EcoFlow API client.

        Args:
            serialnumber: Serial number of the PowerOcean device.
            username: EcoFlow account username (email).
            password: EcoFlow account password.
            variant: PowerOcean device variant / model ID.
            session: Optional aiohttp.ClientSession for making requests.

        """
        self.sn = serialnumber
        self.username = username
        self.password = password
        self.variant = variant

        self.token: str | None = None
        # Needed for the MQTT topics of the account
        self.user_id: str | None = None
        self.api_host: str | None = None

        self._external_session = session
        self._session: aiohttp.ClientSession | None = session

    def _get_session(self) -> aiohttp.ClientSession:
        """Return an existing session or create a new aiohttp session."""
        if self._session:
            return self._session
        self._session = aiohttp.ClientSession()
        return self._session

    def _detail_url(self, host: str) -> str:
        """Return the URL of the device detail response on the given host."""
        return f"https://{host}/provider-service/user/device/detail?sn={self.sn}"

    def _auth_headers(self) -> dict[str, str]:
        """Return the headers of an authorized request."""
        return {
            "authorization": f"Bearer {self.token}",
            "product-type": self.variant,
        }

    async def close(self) -> None:
        """Close internally created aiohttp session."""
        if self._session and not self._external_session:
            await self._session.close()

    async def async_authorize(self) -> None:
        """Authorize user and retrieve authentication token."""
        session = self._get_session()

        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.username,
            "password": base64.b64encode(self.password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            async with asyncio.timeout(10):
                async with session.post(LOGIN_URL, json=data, headers=headers) as resp:
                    resp.raise_for_status()
                    response_data = await resp.json()
        except aiohttp.ClientResponseError as e:
            raise _http_error(e, "Login", "Login rejected") from e
        except (TimeoutError, aiohttp.ClientError) as e:
            msg = "Login failed: EcoFlow cloud not reachable"
            raise EcoflowConnectionError(msg) from e

        # Wrong credentials are answered with HTTP 200 and no token.
        response_body = response_data if isinstance(response_data, dict) else {}
        data = response_body.get("data") or {}
        token = data.get("token")
        if not token:
            msg = f"Login rejected: {response_body.get('message', 'no token')}"
            raise AuthenticationError(msg)

        self.token = token
        user_id = (data.get("user") or {}).get("userId") or data.get("userId")
        self.user_id = str(user_id) if user_id else None
        await self._detect_region()

    async def _detect_region(self) -> None:
        """Detect API region (EU or US) by probing known endpoints."""
        session = self._get_session()
        any_host_reached = False

        for host in self.REGION_HOSTS.values():
            try:
                async with asyncio.timeout(10):
                    async with session.get(
                        self._detail_url(host), headers=self._auth_headers()
                    ) as resp:
                        any_host_reached = True
                        if resp.status == HTTP_OK:
                            self.api_host = host
                            return
            except (TimeoutError, aiohttp.ClientError) as err:
                LOGGER.debug("Region (%s) failed during detection: %s", host, err)
                continue  # Nur bekannte Netzwerk-/Timeout-Fehler abfangen

        if not any_host_reached:
            msg = "Region detection failed: EcoFlow cloud not reachable"
            raise EcoflowConnectionError(msg)

        msg = "Could not detect region"
        raise RegionDetectionError(msg)

    async def fetch_raw(self) -> dict[str, Any]:
        """
        Fetch and validate the device detail response.

        Raises:
            EcoflowConnectionError: On network errors, timeouts or HTTP 5xx.
            AuthenticationError: If the token was rejected (HTTP 401/403).
            EcoflowApiError: If the API answered with an error payload.

        """
        if not self.api_host:
            msg = "Region not detected"
            raise EcoflowApiError(msg)

        session = self._get_session()

        try:
            async with asyncio.timeout(30):
                async with session.get(
                    self._detail_url(self.api_host), headers=self._auth_headers()
                ) as resp:
                    resp.raise_for_status()
                    response = await resp.json()
        except aiohttp.ClientResponseError as e:
            raise _http_error(e, "Fetching data", "Token rejected") from e
        except (TimeoutError, aiohttp.ClientError) as e:
            msg = "Fetching data failed: EcoFlow cloud not reachable"
            raise EcoflowConnectionError(msg) from e

        return validate_response(response)

    async def async_mqtt_credentials(self) -> dict[str, Any]:
        """
        Return the MQTT broker and credentials of the account.

        Same request as the EcoFlow app: url, port, protocol,
        certificateAccount and certificatePassword.

        Raises:
            EcoflowConnectionError: On network errors, timeouts or HTTP 5xx.
            AuthenticationError: If the token was rejected (HTTP 401/403).
            EcoflowApiError: If the answer lacks the credentials.

        """
        session = self._get_session()
        try:
            async with asyncio.timeout(10):
                async with session.get(
                    MQTT_CERTIFICATION_URL, headers=self._auth_headers()
                ) as resp:
                    resp.raise_for_status()
                    response = await resp.json()
        except aiohttp.ClientResponseError as e:
            raise _http_error(e, "MQTT credentials", "Token rejected") from e
        except (TimeoutError, aiohttp.ClientError) as e:
            msg = "MQTT credentials failed: EcoFlow cloud not reachable"
            raise EcoflowConnectionError(msg) from e

        data = validate_response(response)["data"]
        if not all(data.get(key) for key in MQTT_CREDENTIAL_KEYS):
            msg = "MQTT credentials incomplete"
            raise EcoflowApiError(msg)
        return data
