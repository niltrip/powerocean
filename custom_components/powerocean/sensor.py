"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.const import (
    EntityCategory,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
)
from .ecoflow import PowerOceanEndPoint


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    endpoints = data["endpoints"]

    entities = [
        PowerOceanSensor(coordinator, endpoint) for endpoint in endpoints.values()
    ]

    async_add_entities(entities)


class PowerOceanSensor(CoordinatorEntity, SensorEntity):
    """Representation of a PowerOcean Sensor using DataUpdateCoordinator."""

    def __init__(self, coordinator, endpoint: PowerOceanEndPoint) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.endpoint = endpoint
        self._endpoint_id = endpoint.internal_unique_id
        self._endpoint_name = endpoint.name
        self._endpoint_friendly_name = endpoint.friendly_name
        self._endpoint_icon = endpoint.icon
        self._endpoint_description = endpoint.description
        self._endpoint_serial = endpoint.serial
        self._endpoint_device_info = endpoint.device_info

        (
            self._attr_device_class,
            self._attr_native_unit_of_measurement,
            self._attr_state_class,
        ) = getattr(endpoint, "cls", None) or (None, None, None)

        # HA attributes
        self._attr_has_entity_name = True
        # wahrscheinlich diese !!!
        # self._attr_unique_id = self._endpoint_name
        self._attr_unique_id = self._endpoint_id
        # self._unique_id = self._endpoint_id
        self._attr_name = self._endpoint_friendly_name
        self._attr_icon = self._endpoint_icon
        if self._attr_native_unit_of_measurement is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor from coordinator."""
        # LOGGER.debug("Sensor %s native_value data: %s", self._endpoint_name, data)
        return self.coordinator.data.get(self._endpoint_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for this sensor."""
        attr = {}
        if self._endpoint_description:
            attr["product_description"] = self._endpoint_description
        if self._endpoint_serial:
            attr["product_serial"] = self._endpoint_serial
        return attr

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for Home Assistant."""
        return self._endpoint_device_info
