"""
ecoflow.py: Home Assistant adapter for the EcoFlow PowerOcean API client.

This module provides `HAEcoflowApi`, which wires the framework-agnostic
`EcoflowApi` to Home Assistant's shared aiohttp session.

Errors are raised as the exceptions defined in `api.py`; callers map them
to the matching Home Assistant behavior (form error, setup retry, reauth).

Usage:
    api = HAEcoflowApi(hass, serialnumber, username, password, variant)
    await api.async_authorize()
    data = await api.fetch_raw()
"""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import json_loads

from .api import EcoflowApi, validate_response
from .const import (
    LOGGER,
    MOCKED_RESPONSE,
    USE_MOCKED_RESPONSE,
)


class HAEcoflowApi(EcoflowApi):
    """
    Home Assistant wrapper around EcoflowApi.

    Adds HA-specific session handling and the optional mocked response.
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

    async def fetch_raw(self) -> dict[str, Any]:
        """
        Fetch raw device data from the EcoFlow API.

        Optionally replaces the live response with a mocked response.

        Returns:
            The validated API response dictionary.

        Raises:
            EcoflowApiError: If the request fails or the response is invalid.

        """
        api_response = await super().fetch_raw()

        if USE_MOCKED_RESPONSE and MOCKED_RESPONSE.exists():
            LOGGER.warning("Using mocked API response")
            mocked = await self.hass.async_add_executor_job(
                MOCKED_RESPONSE.read_text, "utf-8"
            )
            api_response = validate_response(json_loads(mocked))

        LOGGER.debug("API response received: %s", api_response)
        return api_response
