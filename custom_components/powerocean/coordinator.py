"""
coordinator.py: Home Assistant DataUpdateCoordinator for PowerOcean.

This module defines `PowerOceanCoordinator`, a subclass of
`DataUpdateCoordinator` that periodically fetches live data from
EcoFlow devices using `EcoflowApi` and parses sensor values using
`EcoflowParser`.

It handles:
- Async data fetching
- Automatic periodic updates
- Parsing and returning a dictionary of sensor values

Usage:
  coordinator = PowerOceanCoordinator(hass, api, update_interval=timedelta(seconds=30))
  await coordinator.async_config_entry_first_refresh()
"""

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EcoflowApiError
from .const import LOGGER
from .parser import EcoflowParser

if TYPE_CHECKING:
    from datetime import timedelta

    from homeassistant.core import HomeAssistant

    from .ecoflow import HAEcoflowApi


class PowerOceanCoordinator(DataUpdateCoordinator[dict[str, float | int | str]]):
    """Coordinate periodic fetching and parsing of PowerOcean sensor values."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: HAEcoflowApi,
        update_interval: timedelta,
    ) -> None:
        """Initialize the PowerOcean data update coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name="PowerOcean",
            update_interval=update_interval,
        )
        self.api = api
        self.parser = EcoflowParser(variant=self.api.variant, sn=self.api.sn)

    async def _async_update_data(self) -> dict[str, float | int | str]:
        try:
            response = await self.api.fetch_raw()
            return self.parser.parse_values(response)

        except EcoflowApiError as err:
            LOGGER.debug("API failure during update: %s", err)
            msg = f"API error: {err}"
            raise UpdateFailed(msg) from err

        except RuntimeError as err:
            if "Session is closed" in str(err):
                msg = "Session closed"
                raise UpdateFailed(msg) from err
            raise

        except Exception as err:
            # fallback safety net
            msg = f"Unexpected error: {err}"
            raise UpdateFailed(msg) from err
