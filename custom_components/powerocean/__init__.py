"""__init__.py: The PowerOcean integration."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

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
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PLATFORMS
from .ecoflow import Ecoflow
from .sensor import _update_sensors  # deine Update-Funktion

if TYPE_CHECKING:
    pass

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
        _LOGGER.info("Migrated missing options for %s: %s", entry.title, options)

    polling_interval = timedelta(seconds=options.get(CONF_SCAN_INTERVAL, 10))

    # --- Ecoflow-Objekt initialisieren ---
    device_id = entry.data[CONF_DEVICE_ID]
    model = entry.data[CONF_MODEL_ID]
    ecoflow = Ecoflow(
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model,
        options,
    )

    # --- Authentifizieren und Gerät abrufen ---
    try:
        await hass.async_add_executor_job(ecoflow.authorize)
        ecoflow.device = await hass.async_add_executor_job(ecoflow.get_device)
    except Exception:
        _LOGGER.exception("Error setting up Ecoflow device %s.", entry.title)
        return False

    # --- Ecoflow im Hass-Datenstore speichern ---
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ecoflow

    # # --- Timer für regelmäßige Updates ---
    # async def async_update_data(now: datetime) -> None:
    #     await _update_sensors(hass, ecoflow, device_id, now)

    # # Alten Timer stoppen, falls Reload
    # handles = hass.data.setdefault(DOMAIN, {}).setdefault("update_handles", {})
    # old_handle = handles.pop(entry.entry_id, None)
    # if old_handle:
    #     old_handle()

    # # Neuen Timer starten
    # handles[entry.entry_id] = async_track_time_interval(
    #     hass, async_update_data, polling_interval
    # )

    # # Sofortiger Update-Durchlauf
    # await async_update_data(dt_util.utcnow())

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
        model_id=model,
        configuration_url="https://user-portal.ecoflow.com/",
    )

    # --- Listener für Optionsänderungen registrieren ---
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""

    # Plattformen entladen
    # unload_ok = await hass.config_entries.async_forward_entry_unload(entry, PLATFORMS)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        _LOGGER.warning("Fehler beim Entladen der Plattformen für %s", entry.entry_id)
        return False

    # Ecoflow-Objekt entfernen
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

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

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated (z.B. polling interval)."""
    _LOGGER.debug("Reloading PowerOcean entry %s due to options change", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
