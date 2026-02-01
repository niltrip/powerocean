"""__init__.py: The PowerOcean integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .const import (
    #    _LOGGER,
    DOMAIN,
    PLATFORMS,
)
from .ecoflow import Ecoflow
from .options_flow import PowerOceanOptionsFlowHandler

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001
    """
    Set up the PowerOcean integration.

    Args:
        hass (HomeAssistant): The Home Assistant instance.
        config (ConfigType): The configuration dictionary.

    Returns:
        bool: True if setup was successful, False otherwise.

    """
    # Integration laden
    integration = await async_get_integration(hass, DOMAIN)

    # Zugriff auf manifest.json-Inhalte
    manifest_data = integration.manifest

    name = manifest_data.get("name")
    version = manifest_data.get("version")
    requirements = manifest_data.get("requirements")

    _LOGGER.info(f"Integration '{name}' in Version {version} wird geladen.")
    _LOGGER.debug(f"Benötigte Python-Abhängigkeiten: {requirements}")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    try:
        # Legacy config without options? Patch it.
        updated = False
        options = entry.data["options"]

        if CONF_SCAN_INTERVAL not in options:
            options[CONF_SCAN_INTERVAL] = 10
            updated = True

        if CONF_FRIENDLY_NAME not in options:
            options[CONF_FRIENDLY_NAME] = entry.data.get("options", {}).get(
                "custom_device_name"
            )
            updated = True

        if updated:
            hass.config_entries.async_update_entry(entry, options=options)
            _LOGGER.info(f"Migrated missing options for {entry.title}: {options}")

        device_id = entry.data["user_input"][
            CONF_DEVICE_ID
        ]  # dein eigenes gespeichertes Feld
        model = entry.data["user_input"][CONF_MODEL_ID]
        name = entry.data["options"].get(CONF_FRIENDLY_NAME)

        # Init your device
        ecoflow = Ecoflow(
            entry.data["user_input"][CONF_DEVICE_ID],
            entry.data["user_input"][CONF_EMAIL],
            entry.data["user_input"][CONF_PASSWORD],
            model,
            options,
        )

        # Optional: autorisieren oder Daten abrufen
        await hass.async_add_executor_job(ecoflow.authorize)
        ecoflow.device = await hass.async_add_executor_job(ecoflow.get_device)

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ecoflow
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        device_registry = dr.async_get(hass)

        # Fetching device info from device registry
        # During the config flow, the device info is saved in the entry's data
        # under 'device_info' key.
        device_info = entry.data.get("device_info")

        # If the device_info was provided, register the device
        if device_info:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, device_id)},
                manufacturer=device_info.get("vendor", "ECOFLOW"),
                serial_number=device_id,
                name=name,
                model=device_info.get("product"),
                model_id=model,
                sw_version=device_info.get("version"),
                configuration_url="https://api-e.ecoflow.com",
            )

    except (KeyError, TypeError, AttributeError) as e:
        _LOGGER.exception("Error setting up PowerOcean: %s", e)
        return False
    else:
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
    device_id = entry.data.get("user_input", {}).get(CONF_DEVICE_ID)
    device_name = entry.options.get(CONF_FRIENDLY_NAME)
    if device_id and device_id in hass.data.get(DOMAIN, {}).get(
        "device_specific_sensors", {}
    ):
        hass.data[DOMAIN]["device_specific_sensors"].pop(device_id, None)
        _LOGGER.debug(
            f"{device_id}: Cleared sensor update list for device with custom name "
            f"'{device_name}'"
        )

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_get_options_flow(
    config_entry: ConfigEntry,
) -> PowerOceanOptionsFlowHandler:
    """
    Return the options flow handler for the PowerOcean integration.

    Args:
        config_entry (ConfigEntry): The entry for which to get the options flow.

    Returns:
        PowerOceanOptionsFlowHandler: The options flow handler instance.

    """
    return PowerOceanOptionsFlowHandler(config_entry)
