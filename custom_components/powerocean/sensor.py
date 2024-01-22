from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass

from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_registry import async_get
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry


from homeassistant.config_entries import ConfigEntry

from homeassistant import config_entries
from homeassistant import exceptions

from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import IntegrationError

from datetime import timedelta
from datetime import datetime

import asyncio
from collections import defaultdict

from .const import (
    DOMAIN,
    _LOGGER,
    ATTR_PRODUCT_DESCRIPTION,
    ATTR_DESTINATION_NAME,
    ATTR_SOURCE_NAME,
    ATTR_UNIQUE_ID,
    ATTR_PRODUCT_SERIAL,
    ATTR_PRODUCT_NAME,
    ATTR_PRODUCT_VENDOR,
    ATTR_PRODUCT_BUILD,
    ATTR_PRODUCT_VERSION,
    ATTR_PRODUCT_FEATURES,
    ISSUE_URL_ERROR_MESSAGE
)

from .resolapi import (
    ResolAPI,
    AuthenticationFailed
)



# Setting up the adding and updating of sensor entities
async def async_setup_entry(hass, config_entry, async_add_entities):

    # Retrieve the API instance from the config_entry data
    resol_api = hass.data[DOMAIN][config_entry.entry_id]

    # Fetch the sensor data from the KM2 device
    try:
        # Call the ResolAPI with the detect_device method in case device offline or host changed
        device_check = await hass.async_add_executor_job(resol_api.detect_device)

        if not device_check:
            # If device returns False or is empty, log an error and return
            _LOGGER.warning(f"{resol_api.device['serial']}: It appears the Resol device is offline or has changed host. {data}"+ISSUE_URL_ERROR_MESSAGE)
            return

        # Call the ResolAPI to get the device API data
        data = await hass.async_add_executor_job(resol_api.fetch_data_km2)

        if not data:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(f"{resol_api.device['serial']}: Failed to fetch sensor data - authentication failed or no data. {data}"+ISSUE_URL_ERROR_MESSAGE)
            return

    # Exception if device cannot be found
    except IntegrationError as error:
        _LOGGER.warning(f"{resol_api.device['serial']}: Failed to fetch sensor data: {error}"+ISSUE_URL_ERROR_MESSAGE)
        return
    except AuthenticationFailed as error:
        _LOGGER.warning(f"{resol_api.device['serial']}: Authentication failed: {error}")
        return



    # Get device id and then reset the device specific list of sensors for updates to ensure it's empty before adding new entries
    device_id = resol_api.device['serial']
    _LOGGER.debug(f"{resol_api.device['serial']}: Device ID: {device_id}")

    # Initialize or clear the sensor list for this device
    hass.data[DOMAIN]["device_specific_sensors"][device_id] = []


    #Registering entities to registry, and adding them to list for schedule updates on each device which is stored within hass.data
    for unique_id, endpoint in data.items():
        # Get individul sensor entry from API
        sensor = ResolSensor(resol_api, endpoint)

        # Add sensors to the device specific list of sensors to be updated, via hass.data as also used in unload
        hass.data[DOMAIN]["device_specific_sensors"][device_id].append(sensor)

        # Register sensor
        async_add_entities([sensor], False)


    device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]
    _LOGGER.debug(f"{resol_api.device['serial']}: List of device_specific_sensors[device_id]: {device_specific_sensors[device_id]}")

    # Log the number of sensors registered (and added to the update list)
    _LOGGER.info(f"{resol_api.device['serial']}: All '{len(device_specific_sensors[device_id])}' sensors have registered.")




    # Schedule updates
    async def async_update_data(now):
        # If device deleted but HASS not restarted, then don't bother continuing
        if device_id not in hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}):
            return False

        _LOGGER.debug(f"{resol_api.device['serial']}: Preparing to update sensors at {now}")

        # Fetch the full dataset once from the API
        try:
            full_data = await hass.async_add_executor_job(resol_api.fetch_data_km2)
        except Exception as e:
            _LOGGER.error(f"{resol_api.device['serial']}: Error fetching data from the device: {e}"+ISSUE_URL_ERROR_MESSAGE)
            return

        # Fetch the registry and check if sensors are enabled
        registry = entity_registry.async_get(hass)


        # Set counters to zero
        counter_updated = 0     # Successfully updated sensors
        counter_disabled = 0    # Disabled sensors, not to be updated
        counter_unchanged = 0   # Skipped sensors since value has not changed
        counter_error = 0       # Skipped sensors due to some error, such as registry not found or no data from API

        # Get the list of defice specific sensors from hass.data
        if device_id in hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}):
            device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]

            # Now loop through the sensors to be updated
            for sensor in device_specific_sensors[device_id]:
                entity_id = registry.async_get_entity_id('sensor', DOMAIN, sensor.unique_id)
                if entity_id:
                    entity = registry.entities.get(entity_id)
                    if entity and not entity.disabled_by:
                        sensor_data = full_data.get(sensor.unique_id)
                        _LOGGER.debug(f"{resol_api.device['serial']}: Sensor '{sensor.name}' is not disabled.")
                        if sensor_data:
                            _LOGGER.debug(f"{resol_api.device['serial']}: Sensor '{sensor.name}' has API data eligible for update {sensor_data}")

                            # Check if current state value differs from new API value, or current state has not initialized
                            if str(sensor._state).strip() != str(sensor_data.value).strip():
                                _LOGGER.debug(f"{resol_api.device['serial']}: Sensor '{sensor.name}' marked for update as current value '{sensor._state}' is not the same as new value '{sensor_data.value}'")
                                # Now update the sensor with new values
                                update_status = await sensor.async_update(sensor_data) #update_status returns 1 for upated, 0 for skipped or error
                                counter_updated = counter_updated + update_status
                            else:
                                _LOGGER.debug(f"{resol_api.device['serial']}: Sensor '{sensor.name}' skipped as current value '{sensor._state}' same as new value '{sensor_data.value}'")
                                counter_unchanged = counter_unchanged + 1
                        else:
                            _LOGGER.warning(f"{resol_api.device['serial']}: No update data found for sensor '{sensor.name}'"+ISSUE_URL_ERROR_MESSAGE)
                            counter_error = counter_error + 1
                    else:
                        _LOGGER.debug(f"{resol_api.device['serial']}: Sensor '{sensor.name}' is disabled, skipping update")
                        counter_disabled = counter_disabled + 1
                else:
                    _LOGGER.warning(f"{resol_api.device['serial']}: Sensor '{sensor.name}' not found in the registry, skipping update"+ISSUE_URL_ERROR_MESSAGE)
                    counter_error = counter_error + 1


            # Log summary of updates
            _LOGGER.info(f"{resol_api.device['serial']}: A total of '{counter_updated}' sensors have updated, '{counter_disabled}' are disabled and skipped update, '{counter_unchanged}' sensors value remained constant and '{counter_error}' sensors occured any errors.")

        # Device not in list: must have been deleted, will resolve post re-start
        else:
            _LOGGER.warning(f"{resol_api.device['serial']}: Sensor must have been deleted, re-start of HA recommended.")


    # Get the polling interval from the options, defaulting to 60 seconds if not set
    polling_interval = timedelta(seconds=config_entry.options.get('polling_interval', 60))
    async_track_time_interval(hass, async_update_data, polling_interval)






#This is the actual instance of SensorEntity class
class ResolSensor(SensorEntity):
    """Representation of a RESOL Temperature Sensor."""

    def __init__(self, resol_api: ResolAPI, endpoint):

        """Initialize the sensor."""
        # Make the ResolAPI and the endpoint parameters from the Sensor API available
        self.resol_api = resol_api
        self.endpoint = endpoint

        # Set Friendly name when sensor is first created
        self._attr_unique_id = endpoint.name
        self._attr_has_entity_name = True
        self._attr_name = endpoint.friendly_name
        self._name = endpoint.friendly_name

        # The unique identifier for this sensor within Home Assistant
        # has nothing to do with the entity_id, it is the internal unique_id of the sensor entity registry
        self._unique_id = endpoint.internal_unique_id

        # Set the icon for the sensor based on its unit, ensure the icon_mapper is defined
        self._icon = ResolSensor.icon_mapper.get(endpoint.unit)  # Default handled in function

        # The initial state/value of the sensor
        self._state = endpoint.value

        # The unit of measurement for the sensor
        self._unit = endpoint.unit

        # Set entity category to diagnostic for sensors with no unit
        if resol_api.options.get("group_sensors") and not endpoint.unit:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        # If diagnostics entity then disable sensor by default
        if resol_api.options.get("disable_sensors") and not endpoint.unit:
            self._attr_entity_registry_enabled_default = False


    @property
    def should_poll(self):
        """ async_track_time_intervals handles updates. """
        return False

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name


    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def device_class(self):
        """ Return the device class of this entity, if any. """
        if self._unit == '°C':
            return SensorDeviceClass.TEMPERATURE
        elif self._unit == '%':
            return SensorDeviceClass.POWER_FACTOR
        elif self._unit == 'Wh':
            return SensorDeviceClass.ENERGY
        else:
            return None

    @property
    def state_class(self):
        """ Return the state class of this entity, if any. """
        if self._unit == '°C':
            return SensorStateClass.MEASUREMENT
        elif self._unit == 'h':
            return SensorStateClass.MEASUREMENT
        elif self._unit == 'Wh':
            return SensorStateClass.TOTAL_INCREASING
        else:
            return None

    @property
    def extra_state_attributes(self):
        """ Return the state attributes of this device. """
        attr = {}

        attr[ATTR_PRODUCT_DESCRIPTION] = self.endpoint.description
        attr[ATTR_DESTINATION_NAME] = self.endpoint.destination
        attr[ATTR_SOURCE_NAME] = self.endpoint.source
        attr[ATTR_UNIQUE_ID] = self.endpoint.internal_unique_id
        attr[ATTR_PRODUCT_VENDOR] = self.resol_api.device['vendor']
        attr[ATTR_PRODUCT_NAME] = self.resol_api.device['name']
        attr[ATTR_PRODUCT_SERIAL] = self.endpoint.serial
        attr[ATTR_PRODUCT_VERSION] = self.resol_api.device['version']
        attr[ATTR_PRODUCT_BUILD] = self.resol_api.device['build']
        attr[ATTR_PRODUCT_FEATURES] = self.resol_api.device['features']

        return attr

    @property
    def device_info(self):
        """Return device specific attributes."""
        # Device unique identifier is the serial
        return {
            'identifiers': {(DOMAIN, self.resol_api.device['serial'])},
            'name': self.resol_api.device['name'],
            'manufacturer': 'RESOL',

        }

    icon_mapper = defaultdict(lambda: "mdi:alert-circle", {
        '°C': 'mdi:thermometer',
        '%': 'mdi:flash',
        'l/h': 'mdi:hydro-power',
        'bar': 'mdi:car-brake-low-pressure',
        '%RH': 'mdi:water-percent',
        's': 'mdi:timer',
        'Wh': 'mdi:solar-power-variant-outline',
        'h': 'mdi:timer-sand'
        })


    # This is to register the icon settings
    async def async_added_to_hass(self):
        """Call when the sensor is added to Home Assistant."""
        self.async_write_ha_state()


    # Update of Sensor values
    async def async_update(self, sensor_data=None):
        """Update the sensor with the provided data."""
        if sensor_data is None:
            _LOGGER.warning(f"{self.resol_api.device['serial']}: No new data provided for sensor '{self.name}' update"+ISSUE_URL_ERROR_MESSAGE)
            update_status = 0
            return

        try:
            self._state = sensor_data.value
            update_status = 1
            self.async_write_ha_state()

        except Exception as error:
            _LOGGER.error(f"{self.resol_api.device['serial']}: Error updating sensor {self.name}: {error}"+ISSUE_URL_ERROR_MESSAGE)
            update_status = 0

        return update_status


