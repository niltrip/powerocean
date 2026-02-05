"""__init__.py: The PowerOcean integration."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
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
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.loader import async_get_integration

from .const import DOMAIN, PLATFORMS
from .ecoflow import Ecoflow

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
        manifest = integration.manifest
        name = manifest.get("name")
        version = manifest.get("version")
        requirements = manifest.get("requirements")

        _LOGGER.debug(
            "Loading %s v%s (requirements: %s)",
            name,
            version,
            requirements,
        )

    except Exception:
        _LOGGER.exception("Failed to load the PowerOcean integration")
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    # --- Optionen holen / migrieren ---
    options = dict(entry.options)
    updated = False

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
        _LOGGER.debug("Migrated missing options for %s: %s", entry.title, options)

    # --- Ecoflow-Objekt initialisieren ---
    device_id = entry.data[CONF_DEVICE_ID]
    model_id = entry.data[CONF_MODEL_ID]
    ecoflow = Ecoflow(
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model_id,
        options,
    )

    # --- Authentifizieren und Gerät abrufen ---
    try:
        await hass.async_add_executor_job(ecoflow.authorize)
        ecoflow.device = await hass.async_add_executor_job(ecoflow.get_device)
    except Exception:
        _LOGGER.exception("Error setting up Ecoflow device %s.", entry.title)
        return False

    # --- DataUpdateCoordinator ---
    polling_interval = timedelta(seconds=options.get(CONF_SCAN_INTERVAL, 10))

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"powerocean_{device_id}",
        update_method=lambda: hass.async_add_executor_job(ecoflow.fetch_data),
        update_interval=polling_interval,
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # --- Store in hass.data ---
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "ecoflow": ecoflow,
        "coordinator": coordinator,
    }

    # Sensor-Plattform weiterleiten
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Device Registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_id)},
        manufacturer="ECOFLOW",
        serial_number=device_id,
        name=options.get(CONF_FRIENDLY_NAME),
        model="PowerOcean",
        model_id=model_id,
        configuration_url="https://user-portal.ecoflow.com/",
    )

    # --- Listener für Optionsänderungen registrieren ---
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.warning("Failed to unload platforms for %s", entry.entry_id)
        return False

    # Remove from hass.data
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated (z.B. polling interval)."""
    _LOGGER.debug("Reloading PowerOcean entry %s due to options change", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
