from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import entity_registry
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    _LOGGER,
    ATTR_PRODUCT_BUILD,
    ATTR_PRODUCT_DESCRIPTION,
    ATTR_PRODUCT_FEATURES,
    ATTR_PRODUCT_NAME,
    ATTR_PRODUCT_SERIAL,
    ATTR_PRODUCT_VENDOR,
    ATTR_PRODUCT_VERSION,
    ATTR_UNIQUE_ID,
    DOMAIN,
    ISSUE_URL_ERROR_MESSAGE,
)
from .ecoflow import AuthenticationFailed, Ecoflow


# Setting up the adding and updating of sensor entities
async def async_setup_entry(hass, config_entry, async_add_entities):
    # Retrieve the API instance from the config_entry data
    ecoflow = hass.data[DOMAIN][config_entry.entry_id]
    device_id = ecoflow.device["serial"]

    # Call EcoFlow to get access to the API data
    try:
        auth_check = await hass.async_add_executor_job(ecoflow.authorize)

        if not auth_check:
            # If device returns False or is empty, log an error and return
            _LOGGER.warning(
                f"{device_id}: It appears the PowerOcean device is offline or has changed host."
                + ISSUE_URL_ERROR_MESSAGE
            )

    except AuthenticationFailed as error:
        _LOGGER.warning(f"{device_id}: Authentication failed: {error}")
        return

    try:
        # Fetch the sensor data from the device
        data = await hass.async_add_executor_job(ecoflow.fetch_data)

        if not data:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(
                f"{device_id}: Failed to fetch sensor data => authentication failed or no data."
                + ISSUE_URL_ERROR_MESSAGE
            )
            return

    # Exception if data cannot be fetched
    except IntegrationError as error:
        _LOGGER.warning(
            f"{device_id}: Failed to fetch sensor data: {error}"
            + ISSUE_URL_ERROR_MESSAGE
        )
        return

    # Get device id and then reset the device specific list of sensors for updates
    # to ensure it's empty before adding new entries

    # Initialize or clear the sensor list for this device
    hass.data[DOMAIN]["device_specific_sensors"][device_id] = []

    # Register entities and add them to the list for schedule updates on each device
    # which is stored within hass.data
    for unique_id, endpoint in data.items():
        # Get individual sensor entry from API
        sensor = PowerOceanSensor(ecoflow, endpoint)

        # Add sensors to the device specific list of sensors to be updated, via hass.data as also used in unload
        hass.data[DOMAIN]["device_specific_sensors"][device_id].append(sensor)

        # Register sensor
        async_add_entities([sensor], False)

    device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]
    _LOGGER.debug(
        f"{device_id}: List of device_specific_sensors[device_id]: "
        f"{device_specific_sensors[device_id]}"
    )

    # Log the number of sensors registered (and added to the update list)
    _LOGGER.debug(
        f"{device_id}: All '{len(device_specific_sensors[device_id])}' sensors have registered."
    )

    # Schedule updates
    async def async_update_data(now):
        # If device deleted but HASS not restarted, then don't bother continuing
        if device_id not in hass.data.get(DOMAIN, {}).get(
            "device_specific_sensors", {}
        ):
            return False

        _LOGGER.debug(f"{device_id}: Preparing to update sensors at {now}")

        # Fetch the full dataset once from the API
        try:
            full_data = await hass.async_add_executor_job(ecoflow.fetch_data)

        except Exception as e:
            _LOGGER.error(
                f"{device_id}: Error fetching data from the device: {e}"
                + ISSUE_URL_ERROR_MESSAGE
            )
            return

        # Fetch the registry and check if sensors are enabled
        registry = entity_registry.async_get(hass)

        # Set counters to zero
        counter_updated = 0  # Successfully updated sensors
        counter_disabled = 0  # Disabled sensors, not to be updated
        counter_unchanged = 0  # Skipped sensors since value has not changed
        counter_error = 0  # Skipped sensors due to some error, such as registry not found or no data from API

        # Get the list of device specific sensors from hass.data
        if device_id in hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}):
            device_specific_sensors = hass.data[DOMAIN]["device_specific_sensors"]

            # ----------------------------------------------
            # Now loop through the sensors to be updated
            # ----------------------------------------------
            for sensor in device_specific_sensors[device_id]:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, sensor.unique_id
                )
                if entity_id:
                    entity = registry.entities.get(entity_id)  # get entity
                    # entity is enabled
                    if entity and not entity.disabled_by:
                        sensor_data = full_data.get(sensor.unique_id)
                        # _LOGGER.debug(f"{device_id}: Sensor {sensor.name} enabled.")
                        if sensor_data:
                            # _LOGGER.debug(
                            #     f"{device_id}: Sensor {sensor.name} has API data to update {sensor_data}"
                            # )

                            # Check if current state value differs from new API value,
                            # or current state has not initialized
                            if (
                                str(sensor._state).strip()
                                != str(sensor_data.value).strip()
                            ):
                                # _LOGGER.debug(
                                #     f"{device_id}: Sensor {sensor.name} marked for update: current state = "
                                #     f"{sensor._state} with new value = {sensor_data.value}"
                                # )
                                # Now update the sensor with new values
                                # update_status returns 1 for upated, 0 for skipped or error
                                update_status = await sensor.async_update(sensor_data)
                                counter_updated = counter_updated + update_status
                            else:
                                # _LOGGER.debug(
                                #     f"{device_id}: Sensor {sensor.name} skipped update! Current value = "
                                #     f"{sensor._state}, new value = {sensor_data.value}"
                                # )
                                counter_unchanged = counter_unchanged + 1
                        else:
                            _LOGGER.warning(
                                f"{device_id}: Sensor {sensor.name}: found no data for update!"
                                + ISSUE_URL_ERROR_MESSAGE
                            )
                            counter_error = counter_error + 1
                    else:
                        # _LOGGER.debug(
                        #     f"{device_id}: Sensor {sensor.name} is disabled, skipping update"
                        # )
                        counter_disabled = counter_disabled + 1
                else:
                    _LOGGER.warning(
                        f"{device_id}: Sensor {sensor.name} not found in the registry, skipping update"
                        + ISSUE_URL_ERROR_MESSAGE
                    )
                    counter_error = counter_error + 1

            # Log summary of updates
            _LOGGER.debug(
                f"{device_id}: A total of {counter_updated} sensors have been updated. "
                f"Number of disabled sensors or skipped updates = {counter_disabled} "
                f"Number of sensors with constant values = {counter_unchanged} "
                f"Number of sensors with errors = {counter_error}"
            )

        # Device not in list: must have been deleted, will resolve post re-start
        else:
            _LOGGER.warning(
                f"{device_id}: Sensor must have been deleted, re-start of HA recommended."
            )

    # Get the polling interval from the options, defaulting to 5 seconds if not set
    polling_interval = timedelta(seconds=ecoflow.options.get("polling_interval"))

    async_track_time_interval(hass, async_update_data, polling_interval)


# This is the actual instance of SensorEntity class
class PowerOceanSensor(SensorEntity):
    """Representation of a PowerOcean Sensor."""

    def __init__(self, ecoflow: Ecoflow, endpoint):
        """Initialize the sensor."""
        # Make Ecoflow and the endpoint parameters from the Sensor API available
        self.ecoflow = ecoflow
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
        # Default handled in function
        # self._icon = PowerOceanSensor.icon_mapper.get(endpoint.unit)
        self._icon = endpoint.icon

        # The initial state/value of the sensor
        self._state = endpoint.value

        # The unit of measurement for the sensor
        self._unit = endpoint.unit

        # Set entity category to diagnostic for sensors with no unit
        if not endpoint.unit:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        # If diagnostics entity then disable sensor by default
        if ecoflow.options.get("disable_sensors") and not endpoint.unit:
            self._attr_entity_registry_enabled_default = False

    @property
    def should_poll(self):
        """async_track_time_intervals handles updates."""
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
        """Return the device class of this entity, if any."""
        if self._unit == "°C":
            return SensorDeviceClass.TEMPERATURE
        elif self._unit == "%":
            return SensorDeviceClass.BATTERY
        elif self._unit in {"Wh", "kWh"}:
            return SensorDeviceClass.ENERGY
        elif self._unit == "W":
            return SensorDeviceClass.POWER
        elif self._unit == "V":
            return SensorDeviceClass.VOLTAGE
        elif self._unit == "A":
            return SensorDeviceClass.CURRENT
        else:
            return None

    @property
    def state_class(self):
        """Return the state class of this entity, if any."""
        if self._unit in {"°C", "h", "W", "V", "A"}:
            return SensorStateClass.MEASUREMENT
        elif self._unit in {"Wh", "kWh"}:
            return SensorStateClass.TOTAL_INCREASING
        else:
            return None

    @property
    def extra_state_attributes(self):
        """Return the state attributes of this device."""
        attr = {}

        attr[ATTR_PRODUCT_DESCRIPTION] = self.endpoint.description
        attr[ATTR_UNIQUE_ID] = self.endpoint.internal_unique_id
        attr[ATTR_PRODUCT_VENDOR] = self.ecoflow.device["vendor"]
        attr[ATTR_PRODUCT_NAME] = self.ecoflow.device["name"]
        attr[ATTR_PRODUCT_SERIAL] = self.endpoint.serial
        attr[ATTR_PRODUCT_VERSION] = self.ecoflow.device["version"]
        attr[ATTR_PRODUCT_BUILD] = self.ecoflow.device["build"]
        attr[ATTR_PRODUCT_FEATURES] = self.ecoflow.device["features"]

        return attr

    @property
    def device_info(self):
        """Return device specific attributes."""
        # The unique identifier of the device is the serial number
        return {
            "identifiers": {(DOMAIN, self.ecoflow.device["serial"])},
            "name": self.ecoflow.device["name"],
            "manufacturer": "ECOFLOW",
        }

    @property
    def icon(self):
        """Return the icon of the sensor."""
        return self._icon

    # This is to register the icon settings
    async def async_added_to_hass(self):
        """Call when the sensor is added to Home Assistant."""
        self.async_write_ha_state()

    # Update of Sensor values
    async def async_update(self, sensor_data=None):
        """Update the sensor with the provided data."""
        if sensor_data is None:
            _LOGGER.warning(
                f"{self.ecoflow.device['serial']}: No new data provided for sensor '{self.name}' update"
                + ISSUE_URL_ERROR_MESSAGE
            )
            update_status = 0
            return

        try:
            self._state = sensor_data.value
            update_status = 1
            self.async_write_ha_state()

        except Exception as error:
            _LOGGER.error(
                f"{self.ecoflow.device['serial']}: Error updating sensor {self.name}: {error}"
                + ISSUE_URL_ERROR_MESSAGE
            )
            update_status = 0

        return update_status
