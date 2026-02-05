"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

import logging
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.text import TextEntity
from homeassistant.const import (
    EntityCategory,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
)
from .ecoflow import PowerOceanEndPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities) -> None:
    """Set up PowerOcean sensor entities using the coordinator."""
    entry_data = hass.data[DOMAIN].get(config_entry.entry_id)
    if not entry_data:
        _LOGGER.error("Entry %s not found in hass.data", config_entry.entry_id)
        return

    ecoflow = entry_data["ecoflow"]
    coordinator = entry_data["coordinator"]

    sensors = []
    for endpoint in coordinator.data.values():
        try:
            sensor = PowerOceanSensor(coordinator, ecoflow, endpoint)
            sensors.append(sensor)
        except Exception as err:
            _LOGGER.warning(
                "Failed to create sensor for endpoint %s: %s", endpoint, err
            )

    if sensors:
        async_add_entities(sensors)
        _LOGGER.debug(
            "Registered %d sensors for device %s",
            len(sensors),
            ecoflow.device["serial"],
        )
    else:
        _LOGGER.warning("No sensors created for device %s", ecoflow.device["serial"])


class SensorMapping:
    """Provides mappings from sensor units to HomeAssistant device and state classes."""

    SENSOR_CLASS_MAPPING: ClassVar[dict[str, SensorDeviceClass]] = {
        "°C": SensorDeviceClass.TEMPERATURE,
        "%": SensorDeviceClass.BATTERY,
        "Wh": SensorDeviceClass.ENERGY,
        "kWh": SensorDeviceClass.ENERGY,
        "W": SensorDeviceClass.POWER,
        "V": SensorDeviceClass.VOLTAGE,
        "A": SensorDeviceClass.CURRENT,
        "L": SensorDeviceClass.VOLUME_STORAGE,
    }

    STATE_CLASS_MAPPING: ClassVar[dict[str, SensorStateClass]] = {
        "°C": SensorStateClass.MEASUREMENT,
        "h": SensorStateClass.MEASUREMENT,
        "W": SensorStateClass.MEASUREMENT,
        "V": SensorStateClass.MEASUREMENT,
        "A": SensorStateClass.MEASUREMENT,
        "L": SensorStateClass.MEASUREMENT,
        "Wh": SensorStateClass.MEASUREMENT,
        "kWh": SensorStateClass.TOTAL_INCREASING,
    }

    @staticmethod
    def get_sensor_device_class(unit: str) -> str | None:
        """Gibt die Geräteklasse anhand der Einheit zurück."""
        return SensorMapping.SENSOR_CLASS_MAPPING.get(unit)

    @staticmethod
    def get_sensor_state_class(unit: str) -> str | None:
        """Gibt die State-Klasse anhand der Einheit zurück."""
        return SensorMapping.STATE_CLASS_MAPPING.get(unit, SensorStateClass.MEASUREMENT)


class PowerOceanSensor(CoordinatorEntity, SensorEntity):
    """Representation of a PowerOcean Sensor using DataUpdateCoordinator."""

    def __init__(self, coordinator, ecoflow, endpoint: PowerOceanEndPoint) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._ecoflow = ecoflow
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
        self._attr_unique_id = self._endpoint_name
        # self._attr_unique_id = self._endpoint_id
        # self._unique_id = self._endpoint_id
        self._attr_name = self._endpoint_friendly_name
        self._attr_icon = self._endpoint_icon
        if self._attr_native_unit_of_measurement is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor from coordinator."""
        endpoint = self.coordinator.data.get(self._endpoint_id)
        # _LOGGER.debug("Sensor %s native_value data: %s", self._endpoint_name, data)
        return None if endpoint is None else endpoint.value

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
        if self._endpoint_device_info:
            return self._endpoint_device_info

        return DeviceInfo(
            identifiers={(DOMAIN, self._ecoflow.sn_inverter)},
            name=self._ecoflow.device.get("name"),
            manufacturer="EcoFlow",
            model="PowerOcean",
        )


class PowerOceanText(CoordinatorEntity, TextEntity):
    """Diagnostic / informational text entity for PowerOcean."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, coordinator, ecoflow, endpoint: PowerOceanEndPoint) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._ecoflow = ecoflow
        self._endpoint_id = endpoint.internal_unique_id
        self._endpoint_name = endpoint.name
        self._endpoint_friendly_name = endpoint.friendly_name
        self._endpoint_icon = endpoint.icon
        self._endpoint_description = endpoint.description
        self._endpoint_serial = endpoint.serial
        self._endpoint_device_info = endpoint.device_info

        # HA attributes
        self._attr_unique_id = self._endpoint_name
        self._unique_id = self._endpoint_id
        self._attr_name = self._endpoint_friendly_name
        (
            self._attr_device_class,
            self._attr_native_unit_of_measurement,
            self._attr_state_class,
        ) = getattr(endpoint, "cls", None) or (None, None, None)
        self._attr_icon = endpoint.icon

    @property
    def native_value(self) -> str | None:
        """Return the current value of the sensor from coordinator."""
        endpoint = self.coordinator.data.get(self._endpoint_id)
        return None if endpoint is None else endpoint.value

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for Home Assistant."""
        if self._endpoint_device_info:
            return self._endpoint_device_info

        return DeviceInfo(
            identifiers={(DOMAIN, self._ecoflow.sn_inverter)},
            name=self._ecoflow.device.get("name"),
            manufacturer="EcoFlow",
            model="PowerOcean",
            sw_version=self._ecoflow.device.get("firmware_version"),
        )
