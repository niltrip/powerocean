"""__init__.py: The PowerOcean integration."""  # noqa: EXE002

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import (
    CONF_CHOOSE,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
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
from .options_flow import PowerOceanOptionsFlowHandler

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER.info(STARTUP_MESSAGE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    try:
        # Legacy config without options? Patch it.
        updated = False
        options = dict(entry.options)

        if CONF_SCAN_INTERVAL not in options:
            options[CONF_SCAN_INTERVAL] = 10
            updated = True

        if CONF_FRIENDLY_NAME not in options:
            options[CONF_FRIENDLY_NAME] = entry.data.get("device_info", {}).get(
                "name", "PowerOcean"
            )
            updated = True

        if CONF_CHOOSE not in options:
            options[CONF_CHOOSE] = ["ENERGY_STREAM_REPORT"]  # or a better fallback
            updated = True

        if updated:
            hass.config_entries.async_update_entry(entry, options=options)
            _LOGGER.info(f"Migrated missing options for {entry.title}: {options}")

        # Init your device
        ecoflow = Ecoflow(
            entry.data["user_input"][CONF_DEVICE_ID],
            entry.data["user_input"][CONF_EMAIL],
            entry.data["user_input"][CONF_PASSWORD],
            entry.data["user_input"][CONF_MODEL_ID],
            options,
        )

        # Optional: autorisieren oder Daten abrufen
        await hass.async_add_executor_job(ecoflow.authorize)
        ecoflow.device = await hass.async_add_executor_job(ecoflow.get_device)

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ecoflow
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except Exception as e:
        _LOGGER.exception("Error setting up PowerOcean: %s", e)
        return False


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
            f"{device_id}: Cleared sensor update list for device with custom name "
            f"'{device_name}'"
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
    #             f"Device {device.name} ({device.id}) not removed, it still has entities: "
    #             f"{[e.entity_id for e in entities]}"
    #         )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_get_options_flow(config_entry):
    return PowerOceanOptionsFlowHandler(config_entry)
