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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    PLATFORMS,
)
from .ecoflow import Ecoflow

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """
    Set up the PowerOcean integration.

    Args:
        hass (HomeAssistant): The Home Assistant instance.
        config (ConfigType): The configuration dictionary.

    Returns:
        bool: True if setup was successful, False otherwise.

    """
    try:
        # Integration laden
        integration = await async_get_integration(hass, DOMAIN)

        # Zugriff auf manifest.json-Inhalte
        manifest_data = integration.manifest

        name = manifest_data.get("name")
        version = manifest_data.get("version")
        requirements = manifest_data.get("requirements")

        _LOGGER.info("Integration '%s' in Version %s wird geladen.", name, version)
        _LOGGER.debug("Benötigte Python-Abhängigkeiten: %s", requirements)

    except Exception:
        _LOGGER.exception("Fehler beim Laden der PowerOcean-Integration.")
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    try:
        # Legacy config without options? Patch it.
        updated = False
        options = entry.data.get("options", {})

        if CONF_SCAN_INTERVAL not in options:
            options[CONF_SCAN_INTERVAL] = 10
            updated = True

        if CONF_FRIENDLY_NAME not in options:
            options[CONF_FRIENDLY_NAME] = entry.data.get("options", {}).get(
                "custom_device_name", "PowerOcean"
            )
            updated = True

        if updated:
            hass.config_entries.async_update_entry(entry, options=options)
            _LOGGER.info("Migrated missing options for %s: %s", entry.title, options)

        device_id = entry.data["user_input"][CONF_DEVICE_ID]
        model = entry.data["user_input"][CONF_MODEL_ID]
        name = options.get(CONF_FRIENDLY_NAME)

        # Initialize your device
        ecoflow = Ecoflow(
            device_id,
            entry.data["user_input"][CONF_EMAIL],
            entry.data["user_input"][CONF_PASSWORD],
            model,
            options,
        )

        # Optional: authorize or fetch data
        await hass.async_add_executor_job(ecoflow.authorize)
        ecoflow.device = await hass.async_add_executor_job(ecoflow.get_device)

        # Store device instance
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ecoflow
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        device_registry = dr.async_get(hass)
        device_info = entry.data.get("device_info")

        # If device_info is provided, register the device
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

    except HomeAssistantError:  # Verwenden des generischen Fehlertyps
        _LOGGER.exception("Error setting up PowerOcean for device %s", entry.title)
        return False
    except Exception:
        _LOGGER.exception("Unexpected error setting up PowerOcean.")
        return False
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up device if necessary."""
    try:
        # Unload all platforms
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

        if not unload_ok:
            _LOGGER.warning(
                "Fehler beim Entladen der Plattformen für %s", entry.entry_id
            )
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
                "Sensor-Update-Liste für Gerät mit benutzerdefiniertem Namen '%s' wurde gelöscht: %s",
                device_name,
                device_id,
            )

    except HomeAssistantError:
        # Verwenden von HomeAssistantError für Fehler, die bei der Entladung auftreten können
        _LOGGER.exception("Fehler beim Entladen der PowerOcean-Konfiguration.")
        return False

    except Exception:
        # Allgemeiner Catch-Block für unerwartete Fehler
        _LOGGER.exception(
            "Unvorhergesehener Fehler beim Entladen der PowerOcean-Konfiguration."
        )
        return False

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
