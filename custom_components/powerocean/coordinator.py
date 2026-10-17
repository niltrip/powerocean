"""
coordinator.py: Home Assistant DataUpdateCoordinator for PowerOcean.

This module defines `PowerOceanCoordinator`, a subclass of
`DataUpdateCoordinator` that periodically fetches live data from
EcoFlow devices using `HAEcoflowApi` and parses sensor values using
`EcoflowParser`.

It handles:
- Login during the first refresh (`_async_setup`)
- Automatic periodic updates
- A new login when the EcoFlow cloud rejects the current token
- Mapping API errors to `UpdateFailed` / `ConfigEntryAuthFailed`
- Building the endpoint structure from the first response

Usage:
  coordinator = PowerOceanCoordinator(hass, entry, api)
  await coordinator.async_config_entry_first_refresh()
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, EcoflowApiError, EcoflowConnectionError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER
from .ecoflow import HAEcoflowApi
from .parser import EcoflowParser

if TYPE_CHECKING:
    from .types import PowerOceanEndPoint

type PowerOceanConfigEntry = ConfigEntry[PowerOceanCoordinator]

# Parser errors caused by unexpected API payloads
PARSE_ERRORS = (KeyError, IndexError, TypeError, ValueError, AttributeError)


class PowerOceanCoordinator(DataUpdateCoordinator[dict[str, float | int | str]]):
    """Coordinate periodic fetching and parsing of PowerOcean sensor values."""

    config_entry: PowerOceanConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PowerOceanConfigEntry,
        api: HAEcoflowApi,
    ) -> None:
        """Initialize the PowerOcean data update coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="PowerOcean",
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
        )
        self.api = api
        self.parser = EcoflowParser(variant=self.api.variant, sn=self.api.sn)
        # Filled from the first successful response, read by the platforms
        self.endpoints: dict[str, PowerOceanEndPoint] = {}

    async def _async_setup(self) -> None:
        """Log in once before the first refresh."""
        await self._async_authorize()

    async def _async_update_data(self) -> dict[str, float | int | str]:
        """Holt LIVE-Daten und parsed NUR Values."""
        response = await self._async_fetch()

        try:
            if not self.endpoints:
                self.endpoints = self.parser.parse_structure(response)
            return self.parser.parse_values(response)
        except PARSE_ERRORS as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": repr(err)},
            ) from err

    async def _async_fetch(self) -> dict[str, Any]:
        """Fetch data, logging in again once if the request was rejected."""
        try:
            return await self.api.fetch_raw()
        except EcoflowConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except EcoflowApiError as err:
            # Typically an expired token, reported as HTTP 401/403 or as an
            # error code in an HTTP 200 response.
            LOGGER.debug("Request rejected (%s), logging in again", err)

        await self._async_authorize()

        try:
            return await self.api.fetch_raw()
        except EcoflowApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_authorize(self) -> None:
        """Log in and map errors to the matching Home Assistant behavior."""
        try:
            await self.api.async_authorize()
        except AuthenticationError as err:
            # Starts the reauth flow
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except EcoflowApiError as err:
            # Retried with the next update (or setup retry during first refresh)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
