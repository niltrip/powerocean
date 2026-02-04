"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_PRODUCT_DESCRIPTION,
    ATTR_PRODUCT_SERIAL,
    DOMAIN,
    ISSUE_URL_ERROR_MESSAGE,
)
from .ecoflow import Ecoflow, PowerOceanEndPoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PowerOcean sensor entities for a config entry."""

    # --- Ecoflow-Objekt aus __init__.py holen ---
    ecoflow = hass.data[DOMAIN][config_entry.entry_id]
    if not ecoflow:
        _LOGGER.error(
            "Ecoflow object for entry %s not found, skipping sensor setup",
            config_entry.entry_id,
        )
        return

    device_id = ecoflow.device["serial"] if ecoflow.device else None
    if not device_id:
        _LOGGER.error("Device not initialized yet for entry %s", config_entry.entry_id)
        return

    # --- Initiale Daten abrufen ---
    initial_data = getattr(ecoflow, "initial_data", None)
    if initial_data is None:
        try:
            initial_data = await hass.async_add_executor_job(ecoflow.fetch_data)
            ecoflow.initial_data = initial_data
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch initial data for device %s: %s",
                device_id,
                err,
            )
            initial_data = {}

    # --- Sensoren erzeugen ---
    sensors = []
    for endpoint in initial_data.values():
        try:
            sensor = PowerOceanSensor(ecoflow, endpoint, config_entry.entry_id)
            sensors.append(sensor)
        except Exception as err:
            _LOGGER.warning(
                "Failed to create sensor for endpoint %s: %s", endpoint, err
            )

    if sensors:
        hass.data.setdefault(DOMAIN, {}).setdefault("device_specific_sensors", {})
        hass.data[DOMAIN]["device_specific_sensors"][device_id] = sensors

        async_add_entities(sensors)
        _LOGGER.debug("%s: Registered %d sensors", device_id, len(sensors))
    else:
        _LOGGER.warning("%s: No sensors created for this device", device_id)
        return

    # --- Polling-Intervall aus Optionen ---
    polling_interval = timedelta(
        seconds=config_entry.options.get(CONF_SCAN_INTERVAL, 10)
    )

    # --- Timer-Callback ---
    async def async_update_data(now: datetime) -> None:
        """Update all sensors for this device."""
        await _update_sensors(hass, ecoflow, device_id, now)

    # --- Alten Timer stoppen, falls Reload ---
    update_handles = hass.data.setdefault(DOMAIN, {}).setdefault("update_handles", {})
    old_handle = update_handles.pop(device_id, None)
    if old_handle:
        old_handle()

    # --- Neuen Timer starten ---
    handle = async_track_time_interval(hass, async_update_data, polling_interval)
    update_handles[device_id] = handle

    # --- Sofortiges erstes Update ---
    # await async_update_data(dt_util.utcnow())

    _LOGGER.debug(
        "Scheduled updates every %s for device %s", polling_interval, device_id
    )


async def _fetch_initial_data(
    hass: HomeAssistant, ecoflow: Ecoflow, device_id: str
) -> dict | None:
    """Fetch initial sensor data from the device."""
    try:
        data = await hass.async_add_executor_job(ecoflow.fetch_data)
    except IntegrationError as error:
        _LOGGER.warning(
            "%s: Failed to fetch sensor data: %s%s",
            device_id,
            error,
            ISSUE_URL_ERROR_MESSAGE,
        )
        return None

    if not data:
        _LOGGER.warning(
            "%s: Failed to fetch sensor data => no data.%s",
            device_id,
            ISSUE_URL_ERROR_MESSAGE,
        )
        return None

    return data


def _register_sensors(
    hass: HomeAssistant,
    ecoflow: Ecoflow,
    device_id: str,
    data: dict,
    async_add_entities: Callable[[list, bool], Any],
) -> None:
    """Register sensor entities for a device and add them to Home Assistant."""
    device_sensors = [
        PowerOceanSensor(ecoflow, endpoint, device_id) for endpoint in data.values()
    ]

    hass.data.setdefault(DOMAIN, {}).setdefault("device_specific_sensors", {})
    hass.data[DOMAIN]["device_specific_sensors"][device_id] = device_sensors

    async_add_entities(device_sensors, False)

    _LOGGER.debug(
        "%s: Registered %d sensors: %s",
        device_id,
        len(device_sensors),
        device_sensors,
    )


async def _update_sensors(
    hass: HomeAssistant, ecoflow: Ecoflow, device_id: str, now: date
) -> None:
    """Update all registered sensors for a device."""
    device_sensors = (
        hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}).get(device_id)
    )
    if not device_sensors:
        _LOGGER.warning("%s: No registered sensors found, skipping update", device_id)
        return

    _LOGGER.debug("%s: Updating sensors at %s", device_id, now)

    try:
        full_data = await hass.async_add_executor_job(ecoflow.fetch_data)
    except IntegrationError:
        _LOGGER.exception(
            "%s: Error fetching device data: %s",
            device_id,
            ISSUE_URL_ERROR_MESSAGE,
        )
        return

    registry = entity_registry.async_get(hass)

    counters = {
        "updated": 0,
        "disabled": 0,
        "unchanged": 0,
        "error": 0,
    }

    for sensor in device_sensors:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, sensor.unique_id)
        if not entity_id:
            _LOGGER.warning(
                "%s: Sensor %s not in registry, skipping%s",
                device_id,
                sensor.name,
                ISSUE_URL_ERROR_MESSAGE,
            )
            counters["error"] += 1
            continue

        entity = registry.entities.get(entity_id)
        if entity and entity.disabled_by:
            counters["disabled"] += 1
            continue

        sensor_data = full_data.get(sensor.unique_id)
        if not sensor_data:
            _LOGGER.warning(
                "%s: Sensor %s has no data for update%s",
                device_id,
                sensor.name,
                ISSUE_URL_ERROR_MESSAGE,
            )
            counters["error"] += 1
            continue

        if str(sensor.state).strip() != str(sensor_data.value).strip():
            update_status = await sensor.async_update(sensor_data)
            counters["updated"] += update_status
        else:
            counters["unchanged"] += 1

    _LOGGER.debug(
        "%s: Sensor update summary: %d updated, %d disabled, %d unchanged, %d errors",
        device_id,
        counters["updated"],
        counters["disabled"],
        counters["unchanged"],
        counters["error"],
    )


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


class PowerOceanSensor(SensorEntity):
    """Representation of a PowerOcean Sensor."""

    def __init__(
        self, ecoflow: Ecoflow, endpoint: PowerOceanEndPoint, device_id: str
    ) -> None:
        """Initialize the sensor."""
        # Make Ecoflow and the endpoint parameters from the Sensor API available
        self.ecoflow = ecoflow
        self.endpoint = endpoint
        self.device_id = device_id or ecoflow.sn_inverter

        # Set Friendly name when sensor is first created
        self._attr_unique_id = getattr(endpoint, "name", None)
        self._attr_has_entity_name = True
        self._attr_name = getattr(endpoint, "friendly_name", None)
        self._name = getattr(endpoint, "friendly_name", None)
        self._attr_entity_category = None

        # The unique identifier for this sensor within Home Assistant
        # has nothing to do with the entity_id,
        # it is the internal unique_id of the sensor entity registry
        self._unique_id = getattr(endpoint, "internal_unique_id", None)

        # Default handled in function
        self._icon = getattr(endpoint, "icon", None)

        # The initial state/value of the sensor
        self._state = getattr(endpoint, "value", None)

        # The unit of measurement for the sensor
        self._unit = getattr(endpoint, "unit", None)

        # Set entity category to diagnostic for sensors with no unit
        if not self._unit:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def should_poll(self) -> bool:
        """async_track_time_intervals handles updates."""
        return False

    @property
    def unique_id(self) -> str | None:
        """Return the unique ID of the sensor."""
        return self._unique_id

    @property
    def name(self) -> str | None:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> Any:
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        return self._unit

    @property
    def device_class(self) -> str | None:
        """Return the device class of this entity, if any."""
        if self._unit is not None:
            return SensorMapping.get_sensor_device_class(self._unit)
        return None

    @property
    def state_class(self) -> str | None:
        """Return the state class of this entity, if any."""
        if self._unit is not None:
            return SensorMapping.get_sensor_state_class(self._unit)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes specific to this sensor."""
        attr = {}

        # Sensor-spezifische Infos
        if getattr(self.endpoint, "description", None):
            attr[ATTR_PRODUCT_DESCRIPTION] = self.endpoint.description

        if getattr(self.endpoint, "serial", None):
            attr[ATTR_PRODUCT_SERIAL] = self.endpoint.serial

        return attr

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device specific attributes."""
        # 🔥 Sub-Device (Battery / Wallbox)
        if getattr(self.endpoint, "device_info", None):
            return self.endpoint.device_info

        # 🔁 Fallback: Inverter
        if not self.ecoflow.device:
            return None

        inverter_sn = self.ecoflow.sn_inverter

        return DeviceInfo(
            identifiers={(DOMAIN, inverter_sn)},
            name=self.ecoflow.device.get("name"),
            manufacturer="EcoFlow",
            model="PowerOcean",
        )

    @property
    def icon(self) -> str | None:
        """Return the icon of the sensor."""
        return self._icon

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
                "%s: No new data provided for sensor '%s' update%s",
                serial,
                self.name,
                ISSUE_URL_ERROR_MESSAGE,
            )
            update_status = 0
            return None

        try:
            self._state = sensor_data.value
            update_status = 1
            self.async_write_ha_state()

        except (AttributeError, TypeError) as error:
            serial = self.ecoflow.device["serial"] if self.ecoflow.device else "unknown"
            _LOGGER.exception(
                "%s: Error updating sensor %s%s",
                serial,
                self.name,
                ISSUE_URL_ERROR_MESSAGE,
            )
            update_status = 0

        return update_status
