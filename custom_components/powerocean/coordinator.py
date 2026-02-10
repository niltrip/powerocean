from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.powerocean.const import LOGGER
from custom_components.powerocean.ecoflow import EcoflowApi

_LOGGER = LOGGER


class PowerOceanCoordinator(DataUpdateCoordinator[dict[str, float | int | str]]):
    """Coordinate periodic fetching and parsing of PowerOcean sensor values."""

    def __init__(
        self, hass: HomeAssistant, api: EcoflowApi, update_interval: timedelta
    ) -> None:
        """Initialize the PowerOcean data update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="PowerOcean",
            update_interval=update_interval,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, float | int | str]:
        """Holt LIVE-Daten und parsed NUR Values."""
        response = await self.api.fetch_raw()
        return self.api.parse_values(response)
