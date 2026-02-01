"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

# Setting up the adding and updating of sensor entities
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import entity_registry
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    _LOGGER,
    ATTR_PRODUCT_DESCRIPTION,
    ATTR_UNIQUE_ID,
    DOMAIN,
    ISSUE_URL_ERROR_MESSAGE,
)
from .ecoflow import AuthenticationFailedError, Ecoflow, PowerOceanEndPoint


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: Callable[[list, bool], Any],
) -> None:
    """
    Set up PowerOcean sensor entities for a config entry.

    This function initializes and registers sensor entities, schedules periodic updates,
    and manages device-specific sensor lists for the PowerOcean integration.
    """
    ecoflow = hass.data[DOMAIN][config_entry.entry_id]
    _LOGGER.debug(f"ecoflow.device: {ecoflow.device}")
    device_id = ecoflow.device["serial"]

    if not await _authorize_device(hass, ecoflow, device_id):
        return

    data = await _fetch_initial_data(hass, ecoflow, device_id)
    if not data:
        return

    _register_sensors(hass, ecoflow, device_id, data, async_add_entities)

    polling_interval = timedelta(
        seconds=config_entry.options.get(CONF_SCAN_INTERVAL, 10)
    )

    async def async_update_data(now: date) -> None:
        await _update_sensors(hass, ecoflow, device_id, now)

    async_track_time_interval(hass, async_update_data, polling_interval)


async def _authorize_device(
    hass: HomeAssistant, ecoflow: Ecoflow, device_id: str
) -> bool:
    """Authorize the device and log warnings if authorization fails."""
    try:
        auth_check = await hass.async_add_executor_job(ecoflow.authorize)
        if not auth_check:
            _LOGGER.warning(
                f"{device_id}: PowerOcean device is offline or has changed host."
                + ISSUE_URL_ERROR_MESSAGE
            )
            return False
    except AuthenticationFailedError as error:
        _LOGGER.warning(f"{device_id}: Authentication failed: {error}")
        return False
    else:
        return True


async def _fetch_initial_data(
    hass: HomeAssistant, ecoflow: Ecoflow, device_id: str
) -> dict | None:
    """Fetch initial sensor data from the device."""
    try:
        data = await hass.async_add_executor_job(ecoflow.fetch_data)
        if not data:
            _LOGGER.warning(
                f"{device_id}: Failed to fetch sensor data => no data."
                + ISSUE_URL_ERROR_MESSAGE
            )
            return None
    except IntegrationError as error:
        _LOGGER.warning(
            f"{device_id}: Failed to fetch sensor data: {error}"
            + ISSUE_URL_ERROR_MESSAGE
        )
        return None
    else:
        return data


def _register_sensors(
    hass: HomeAssistant,
    ecoflow: Ecoflow,
    device_id: str,
    data: dict,
    async_add_entities: Callable[[list, bool], Any],
) -> None:
    """Register sensor entities and add them to the device-specific list."""
    # ✅ Sicherstellen, dass device_specific_sensors existiert
    hass.data.setdefault(DOMAIN, {}).setdefault("device_specific_sensors", {})
    hass.data[DOMAIN]["device_specific_sensors"][device_id] = []
    for endpoint in data.values():
        sensor = PowerOceanSensor(ecoflow, endpoint, device_id)
        hass.data[DOMAIN]["device_specific_sensors"][device_id].append(sensor)
        async_add_entities([sensor], False)  # noqa: FBT003

    device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]
    _LOGGER.debug(
        f"{device_id}: List of device_specific_sensors[device_id]: "
        f"{device_specific_sensors[device_id]}"
    )
    _LOGGER.debug(
        f"{device_id}: All '{len(device_specific_sensors[device_id])}' "
        "sensors have registered."
    )


async def _update_sensors(
    hass: HomeAssistant, ecoflow: Ecoflow, device_id: str, now: date
) -> bool | None:
    """Update all registered sensors for the device."""
    if device_id not in hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}):
        return False

    _LOGGER.debug(f"{device_id}: Preparing to update sensors at {now}")

    try:
        full_data = await hass.async_add_executor_job(ecoflow.fetch_data)
    except IntegrationError as e:
        _LOGGER.error(
            f"{device_id}: Error fetching data from the device: {e}"
            + ISSUE_URL_ERROR_MESSAGE
        )
        return None

    registry = entity_registry.async_get(hass)
    device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]

    counter_updated = 0
    counter_disabled = 0
    counter_unchanged = 0
    counter_error = 0

    for sensor in device_specific_sensors[device_id]:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, sensor.unique_id)
        if entity_id:
            entity = registry.entities.get(entity_id)
            if entity and not entity.disabled_by:
                sensor_data = full_data.get(sensor.unique_id)
                if sensor_data:
                    if str(sensor.state).strip() != str(sensor_data.value).strip():
                        update_status = await sensor.async_update(sensor_data)
                        counter_updated += update_status
                    else:
                        counter_unchanged += 1
                else:
                    _LOGGER.warning(
                        f"{device_id}: Sensor {sensor.name}: found no data for update!"
                        + ISSUE_URL_ERROR_MESSAGE
                    )
                    counter_error += 1
            else:
                counter_disabled += 1
        else:
            _LOGGER.warning(
                f"{device_id}: Sensor {sensor.name} not in registry, skipping update"
                + ISSUE_URL_ERROR_MESSAGE
            )
            counter_error += 1

    _LOGGER.debug(
        f"{device_id}: A total of {counter_updated} sensors have been updated. "
        f"Number of disabled sensors or skipped updates = {counter_disabled} "
        f"Number of sensors with constant values = {counter_unchanged} "
        f"Number of sensors with errors = {counter_error}"
    )
    return None


class PowerOceanSensor(SensorEntity):
    """Representation of a PowerOcean Sensor."""

    def __init__(
        self, ecoflow: Ecoflow, endpoint: PowerOceanEndPoint, device_id: str
    ) -> None:
        """Initialize the sensor."""
        # Make Ecoflow and the endpoint parameters from the Sensor API available
        self.ecoflow = ecoflow
        self.endpoint = endpoint

        # Set Friendly name when sensor is first created
        self._attr_has_entity_name = True
        self._attr_name = getattr(endpoint, "friendly_name", None)
        self._attr_entity_category = None
        # async_track_time_intervals handles updates
        self._attr_should_poll = False

        # The unique identifier for this sensor within Home Assistant
        # has nothing to do with the entity_id,
        # it is the internal unique_id of the sensor entity registry
        self._attr_unique_id = getattr(endpoint, "internal_unique_id", None)

        # Default handled in function
        self._attr_icon = getattr(endpoint, "icon", None)

        # The initial state/value of the sensor
        self._attr_native_value = getattr(endpoint, "value", None)

        (
            self._attr_device_class,
            self._attr_native_unit_of_measurement,
            self._attr_state_class,
        ) = getattr(endpoint, "class_", None) or (None, None, None)

        # Set entity category to diagnostic for sensors with no unit
        if self._attr_native_unit_of_measurement is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self._attr_extra_state_attributes = {
            ATTR_PRODUCT_DESCRIPTION: getattr(self.endpoint, "description", None),
            ATTR_UNIQUE_ID: getattr(self.endpoint, "internal_unique_id", None),
        }

        device_name = (
            self.ecoflow.device["name"]
            if self.ecoflow.device and "name" in self.ecoflow.device
            else None
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "EcoFlow",
            "model": "PowerOcean",
        }  # The unique identifier of the device is the serial number

    # This is to register the icon settings
    async def async_added_to_hass(self) -> None:
        """Call when the sensor is added to Home Assistant."""
        self.async_write_ha_state()

    # Update of Sensor values
    async def async_update(
        self, sensor_data: PowerOceanEndPoint | None = None
    ) -> int | None:
        """Update the sensor with the provided data."""
        if sensor_data is None:
            serial = self.ecoflow.device["serial"] if self.ecoflow.device else "unknown"
            _LOGGER.warning(
                f"{serial}: No new data provided for sensor '{self.name}' update"
                + ISSUE_URL_ERROR_MESSAGE
            )
            update_status = 0
            return None

        try:
            self._attr_native_value = sensor_data.value
            update_status = 1
            self.async_write_ha_state()

        except (AttributeError, TypeError) as error:
            serial = self.ecoflow.device["serial"] if self.ecoflow.device else "unknown"
            _LOGGER.error(
                f"{serial}: Error updating sensor {self.name}: {error}"
                + ISSUE_URL_ERROR_MESSAGE
            )
            update_status = 0

        return update_status
