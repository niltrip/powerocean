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

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, IntegrationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import json_loads
from pydantic import Json

from .api import (
    AuthenticationError,
    EcoflowApi,
    EcoflowApiError,
)
from .const import (
    LOGGER,
    MOCKED_RESPONSE,
    USE_MOCKED_RESPONSE,
)

HTTP_OK = 200


class HAEcoflowApi(EcoflowApi):
    """
    Home Assistant wrapper around EcoflowApi.

    Adds HA-specific session handling and exception mapping.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        serialnumber: str,
        username: str,
        password: str,
        variant: str,
    ) -> None:
        """
        Initialize the Home Assistant EcoFlow API adapter.

        Args:
            hass: Home Assistant instance.
            serialnumber: Device serial number.
            username: EcoFlow account email.
            password: EcoFlow account password.
            variant: Device product type identifier.

        """
        session = async_get_clientsession(hass)
        super().__init__(
            serialnumber=serialnumber,
            username=username,
            password=password,
            variant=variant,
            session=session,
        )
        self.hass = hass

    async def async_authorize(self) -> None:
        """
        Authorize against the EcoFlow API and map errors to HA exceptions.

        Raises:
            IntegrationError: If credentials are invalid.
            ConfigEntryNotReady: If the API is temporarily unavailable.

        """
        try:
            await super().async_authorize()
        except AuthenticationError as e:
            msg = "Invalid username or password"
            raise IntegrationError(msg) from e
        except EcoflowApiError as e:
            raise ConfigEntryNotReady from e

    async def fetch_raw(self) -> dict[str, Any]:
        """
        Fetch raw device data from the EcoFlow API.

        Optionally replaces the live response with a mocked response
        and validates the returned payload structure.

        Returns:
            The validated API response dictionary.

        Raises:
            IntegrationError: If the response structure is invalid.

        """
        api_response = await super().fetch_raw()

        if USE_MOCKED_RESPONSE and MOCKED_RESPONSE.exists():
            LOGGER.warning("Using mocked API response")

            def load_mock_file() -> Json:
                return json_loads(MOCKED_RESPONSE.read_text(encoding="utf-8"))

            api_response = load_mock_file()

        LOGGER.debug("API response received: %s", api_response)
        return self._validate_response(api_response)

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
