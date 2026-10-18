"""
ecoflow.py: Home Assistant adapter for the EcoFlow PowerOcean API client.

This module provides `HAEcoflowApi`, which wires the framework-agnostic
`EcoflowApi` to Home Assistant's shared aiohttp session.

Errors are raised as the exceptions defined in `api.py`; callers map them
to the matching Home Assistant behavior (form error, setup retry, reauth).

For development, the environment variable POWEROCEAN_MOCK_RESPONSE replaces
the EcoFlow cloud with a saved response (see `mock_response_path`).

Usage:
    api = HAEcoflowApi(hass, serialnumber, username, password, variant)
    await api.async_authorize()
    data = await api.fetch_raw()
"""

import os
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import json_loads

from .api import EcoflowApi, EcoflowConnectionError, validate_response
from .const import LOGGER

MOCK_RESPONSE_ENV = "POWEROCEAN_MOCK_RESPONSE"
FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


def mock_response_path() -> Path | None:
    """
    Return the saved response to use instead of the cloud, if one is set.

    Development only: start Home Assistant with e.g.
    POWEROCEAN_MOCK_RESPONSE=response_modified_po_dual.json. A bare file name
    is looked up in tests/fixtures. Login and requests are then skipped, so
    any serial number and password work.
    """
    name = os.environ.get(MOCK_RESPONSE_ENV)
    if not name:
        return None
    path = Path(name)
    if not path.exists() and (FIXTURES_DIR / name).exists():
        path = FIXTURES_DIR / name
    return path


def _read_mock_response(path: Path) -> dict[str, Any]:
    """Return the API response saved in `path`, also from a diagnostics download."""
    try:
        data = json_loads(path.read_text("utf-8"))
    except (OSError, ValueError) as err:
        msg = f"Mock response {path} could not be read: {err}"
        raise EcoflowConnectionError(msg) from err
    diagnostics = data.get("data") if isinstance(data, dict) else None
    if isinstance(diagnostics, dict) and "api_response" in diagnostics:
        data = diagnostics["api_response"]
    return validate_response(data)


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
        self.mock_response = mock_response_path()

    async def async_authorize(self) -> None:
        """Log in, unless a mocked response replaces the cloud."""
        if self.mock_response is not None:
            LOGGER.warning(
                "Using mocked API response %s instead of the EcoFlow cloud",
                self.mock_response,
            )
            self.api_host = "mock"
            return
        await super().async_authorize()

    async def fetch_raw(self) -> dict[str, Any]:
        """
        Fetch raw device data from the EcoFlow API, or the mocked response.

        Returns:
            The validated API response dictionary.

        Raises:
            EcoflowApiError: If the request fails or the response is invalid.

        """
        if self.mock_response is not None:
            api_response = await self.hass.async_add_executor_job(
                _read_mock_response, self.mock_response
            )
        else:
            api_response = await super().fetch_raw()

        LOGGER.debug("API response received: %s", api_response)
        return api_response
