"""__init__.py: The PowerOcean integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS, _LOGGER, DOMAIN, ISSUE_URL_ERROR_MESSAGE, STARTUP_MESSAGE
from .ecoflow import Ecoflow


_LOGGER.info(STARTUP_MESSAGE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""

    # Setup DOMAIN as default
    hass.data.setdefault(DOMAIN, {})

    # Setup device specific sensor list (used in updates) on HASS so it is available within integration (reuqired for unload)
    hass.data[DOMAIN]["device_specific_sensors"] = {}

    # Store an instance of the API instance in hass.data[domain]
    user_input = entry.data["user_input"]        # This user_input object was stored after the device
                                                 # was setup and has the user/pass/serial info
    device_info = entry.data.get("device_info")  # This device_info object was stored after the device
                                                 # was setup and has the name and serial needed etc.
    options = entry.data["options"]              # These are the options during setup, including custom device name
    ecoflow = Ecoflow(user_input["serialnumber"], user_input["username"], user_input["password"])

    if device_info:
        ecoflow.device = device_info   # Store the device information
        ecoflow.options = options      # Store the options
    hass.data[DOMAIN][entry.entry_id] = ecoflow

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Get the device registry
    device_registry = dr.async_get(hass)

    # Fetching device info from device registry
    # During the config flow, the device info is saved in the entry's data under 'device_info' key.
    device_info = entry.data.get("device_info")

    # If the device_info was provided, register the device
    if device_info:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device_info["serial"])},
            manufacturer=device_info.get("vendor", "ECOFLOW"),
            serial_number=device_info.get("serial"),
            name=options.get("custom_device_name"),  # Custom device name from user step 2 (options)
            model=device_info.get("product"),
            sw_version=device_info.get("version"),
            configuration_url="https://api-e.ecoflow.com",
            suggested_area="Boiler Room",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload all platforms associated with this entry
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up hass.data if any reference exists
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # Additionally, clear the device-specific sensors list if it exists
        device_id = entry.data.get("device_info").get("serial")
        device_name = entry.data.get("options").get("custom_device_name")
        if device_id in hass.data.get(DOMAIN, {}).get("device_specific_sensors", {}):
            hass.data[DOMAIN]["device_specific_sensors"].pop(device_id, None)
            _LOGGER.debug(
                f"{device_id}: Cleared sensor update list for device with custom name '{device_name}'"
            )

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
