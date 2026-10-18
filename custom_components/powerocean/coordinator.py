"""
coordinator.py: Home Assistant DataUpdateCoordinator for PowerOcean.

This module defines `PowerOceanCoordinator`, a subclass of
`DataUpdateCoordinator` that periodically fetches live data from
EcoFlow devices using `HAEcoflowApi` and parses them with `parse()`.

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
from .parse import parse
from .types import PowerOceanEndPoint

if TYPE_CHECKING:
    from .errors import ActiveError

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
        # Filled from the first successful response, read by the platforms
        self.endpoints: dict[str, PowerOceanEndPoint] = {}
        # Last raw API response, for diagnostics
        self.last_response: dict[str, Any] | None = None
        # Active errors, by unique_id of the error count sensor
        self.active_errors: dict[str, list[ActiveError]] = {}

    async def _async_setup(self) -> None:
        """Log in once before the first refresh."""
        await self._async_authorize()

    async def _async_update_data(self) -> dict[str, float | int | str]:
        """Fetch and parse the live data; the endpoints come from the first one."""
        response = await self._async_fetch()
        self.last_response = response

        try:
            result = parse(response, self.api.sn, self.api.variant)
            if not self.endpoints:
                self.endpoints = {
                    unique_id: PowerOceanEndPoint.from_datapoint(datapoint)
                    for unique_id, datapoint in result.first().items()
                }
        except PARSE_ERRORS as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": repr(err)},
            ) from err

        self.active_errors = result.active_errors
        return result.values()

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
