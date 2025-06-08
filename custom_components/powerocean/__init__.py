"""__init__.py: The PowerOcean integration."""  # noqa: EXE002

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import (
    _LOGGER,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)
from .ecoflow import Ecoflow

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER.info(STARTUP_MESSAGE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    # Setup DOMAIN as default
    hass.data.setdefault(DOMAIN, {})
    # Setup device specific sensor list (used in updates) on HASS so it is
    # available within the integration (required for unload).
    hass.data[DOMAIN]["device_specific_sensors"] = {}
    # Extract user_input and options from entry.data and entry.options
    user_input = entry.data.get("user_input", {})
    device_info = entry.data.get("device_info", {})
    options = entry.data.get("options", {})

    _LOGGER.debug(f"User input: {options}")
    if not user_input:
        _LOGGER.error("User input is missing in the config entry data.")
        return False

    ecoflow = Ecoflow(
        user_input.get(CONF_DEVICE_ID, {}),
        user_input.get(CONF_EMAIL, {}),
        user_input.get(CONF_PASSWORD, {}),
        user_input.get(CONF_MODEL_ID, {}),
        options,
    )

    # Fetching device info from device registry
    # During the config flow, the device info is saved in the entry's data under
    # the 'device_info' key.
    device_info = entry.data.get("device_info")

    if device_info:
        ecoflow.device = device_info  # Store the device information

    hass.data[DOMAIN][entry.entry_id] = ecoflow
    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Get the device registry
    device_registry = dr.async_get(hass)
    # If the device_info was provided, register the device
    if device_info:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            configuration_url="https://api-e.ecoflow.com",
            name=options.get(CONF_FRIENDLY_NAME) if options else None,
            serial_number=device_info.get("serial"),
            model=device_info.get("product"),
            model_id=user_input.get(CONF_MODEL_ID),
            sw_version=device_info.get("version"),
            identifiers={(DOMAIN, device_info["serial"])},
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up device if necessary."""

    # Unload all platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if not unload_ok:
        _LOGGER.warning(f"Failed to unload platforms for {entry.entry_id}")
        return False

    # Clean up hass.data
    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Optional cleanup of custom sensor mapping
    device_id = entry.data.get("device_info", {}).get(CONF_DEVICE_ID)
    device_name = entry.options.get(CONF_FRIENDLY_NAME)
    if device_id and device_id in hass.data.get(DOMAIN, {}).get(
        "device_specific_sensors", {}
    ):
        hass.data[DOMAIN]["device_specific_sensors"].pop(device_id, None)
        _LOGGER.debug(
            f"{device_id}: Cleared sensor update list for device with custom name '{device_name}'"
        )
    # Optional: Clean up device registry if no entities are left
    # # Clean up device registry if no entities are left
    # device_registry = await async_get_device_registry(hass)
    # entity_registry = await async_get_entity_registry(hass)

    # # entity_registry = async_get_entity_registry(hass)

    # serial = entry.data.get("device_info", {}).get("serial")
    # if not serial:
    #     _LOGGER.debug(f"No serial found in entry.data, skipping device cleanup.")
    #     return True

    # device = device_registry.async_get_device(identifiers={(DOMAIN, serial)})
    # if device:
    #     entities = entity_registry.entities_for_device(
    #         device.id, include_disabled_entities=True
    #     )
    #     if not entities:
    #         _LOGGER.debug(
    #             f"Removing device {device.name} ({device.id}) from device registry."
    #         )
    #         device_registry.async_remove_device(device.id)
    #     else:
    #         _LOGGER.debug(
    #             f"Device {device.name} ({device.id}) not removed because it still has entities: "
    #             f"{[e.entity_id for e in entities]}"
    #         )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
