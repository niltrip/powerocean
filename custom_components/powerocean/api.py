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
    AuthenticationError: Raised on login failure.
    RegionDetectionError: Raised if API region cannot be detected.
"""

import asyncio
import base64
from typing import Any, ClassVar

import aiohttp

from .const import LOGGER

HTTP_OK = 200


class EcoflowApiError(Exception):
    """Base exception for all EcoFlow API errors."""


class AuthenticationError(EcoflowApiError):
    """Raised when authentication with the EcoFlow API fails."""


class RegionDetectionError(EcoflowApiError):
    """Raised when automatic API region detection fails."""


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
        self.api_host: str | None = None
        self.url_authorize = "https://api.ecoflow.com/auth/login"

        self._external_session = session
        self._session: aiohttp.ClientSession | None = session

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return an existing session or create a new aiohttp session."""
        if self._session:
            return self._session
        self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close internally created aiohttp session."""
        if self._session and not self._external_session:
            await self._session.close()

    async def async_authorize(self) -> None:
        """Authorize user and retrieve authentication token."""
        session = await self._get_session()

        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.username,
            "password": base64.b64encode(self.password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            async with asyncio.timeout(10):
                async with session.post(
                    self.url_authorize, json=data, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    response_data = await resp.json()
        except Exception as e:
            msg = "Login failed"
            raise AuthenticationError(msg) from e

        token = response_data.get("data", {}).get("token")
        if not token:
            msg = "Missing token in response"
            raise AuthenticationError(msg)

        self.token = token
        await self._detect_region()

    async def _detect_region(self) -> None:
        """Detect API region (EU or US) by probing known endpoints."""
        session = await self._get_session()

        for host in self.REGION_HOSTS.values():
            url = f"https://{host}/provider-service/user/device/detail?sn={self.sn}"

            headers = {
                "authorization": f"Bearer {self.token}",
                "product-type": self.variant,
            }

            try:
                async with asyncio.timeout(10):
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == HTTP_OK:
                            self.api_host = host
                            return
            except (TimeoutError, aiohttp.ClientError) as err:
                LOGGER.debug("Region (%s) failed during detection: %s", host, err)
                continue  # Nur bekannte Netzwerk-/Timeout-Fehler abfangen

        msg = "Could not detect region"
        raise RegionDetectionError(msg)

    async def fetch_raw(self) -> dict[str, Any]:
        """Fetch data from Url (Async version)."""
        if not self.api_host:
            msg = "Region not detected"
            raise EcoflowApiError(msg)

        session = await self._get_session()

        url = (
            f"https://{self.api_host}/provider-service/user/device/detail?sn={self.sn}"
        )

        headers = {
            "authorization": f"Bearer {self.token}",
            "product-type": self.variant,
        }

        async with asyncio.timeout(30):
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json()
