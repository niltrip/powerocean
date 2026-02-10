"""__init__.py: The PowerOcean integration."""

from datetime import timedelta
from pyexpat import model

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
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PLATFORMS,
    PowerOceanModel,
)
from .coordinator import PowerOceanCoordinator
from .ecoflow import EcoflowApi


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

        LOGGER.debug(
            "Loading %s v%s (requirements: %s)",
            name,
            version,
            requirements,
        )

    except Exception:
        LOGGER.exception("Failed to load the PowerOcean integration")
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    # --- Optionen holen / migrieren ---
    options = dict(entry.options)
    updated = False

    if CONF_SCAN_INTERVAL not in options:
        options[CONF_SCAN_INTERVAL] = entry.data.get("options").get(
            "scan_interval", DEFAULT_SCAN_INTERVAL
        )
        updated = True

    if CONF_FRIENDLY_NAME not in options:
        options[CONF_FRIENDLY_NAME] = entry.data.get("options", {}).get(
            "friendly_name", DEFAULT_NAME
        )
        updated = True

    if updated:
        hass.config_entries.async_update_entry(entry, options=options)
        LOGGER.debug("Migrated missing options for %s: %s", entry.title, options)

    # --- Ecoflow-Objekt initialisieren ---

    device_id = entry.data[CONF_DEVICE_ID]
    if not device_id:
        msg = "Missing device_id in config entry"
        raise ConfigEntryNotReady(msg)

    model_id = entry.data[CONF_MODEL_ID]
    if not model_id:
        msg = "Missing model_id in config entry"
        raise ConfigEntryNotReady(msg)

    api = EcoflowApi(
        hass,
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model_id,
    )

    # --- Authentifizieren und Gerät abrufen ---
    try:
        # await hass.async_add_executor_job(ecoflow.authorize)
        await api.async_authorize()
        # api.device = await hass.async_add_executor_job(api.get_device)
    except Exception:
        LOGGER.exception("Error setting up Ecoflow device %s.", entry.title)
        return False

    # EINMALIG: Struktur aufbauen
    raw = await api.fetch_raw()
    LOGGER.debug("fetch_raw returned %s", raw)
    endpoints = api.parse_structure(raw)

    # --- DataUpdateCoordinator ---
    scan_interval = timedelta(
        seconds=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    coordinator = PowerOceanCoordinator(
        hass=hass,
        api=api,
        update_interval=scan_interval,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "endpoints": endpoints,
    }

    # Sensor-Plattform weiterleiten
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    model_name = MODEL_NAME_MAP[PowerOceanModel(model_id)]

    # Device Registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_id)},
        manufacturer="EcoFlow",
        serial_number=device_id,
        name=options.get(CONF_FRIENDLY_NAME),
        model=model_name,
        model_id=model_id,
        configuration_url="https://user-portal.ecoflow.com/",
    )

    # --- Listener für Optionsänderungen registrieren ---
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to new structure."""
    version = entry.version

    if version == 1.3:
        data = dict(entry.data)
        options = dict(entry.options)

        # ALT → NEU
        if "user_input" in data:
            old = data.pop("user_input")

            data.update(
                {
                    CONF_DEVICE_ID: old.get(CONF_DEVICE_ID),
                    CONF_EMAIL: old.get(CONF_EMAIL),
                    CONF_PASSWORD: old.get(CONF_PASSWORD),
                    CONF_MODEL_ID: old.get(CONF_MODEL_ID),
                }
            )

            # Optionen auslagern
            options.setdefault(
                CONF_FRIENDLY_NAME,
                old.get(CONF_FRIENDLY_NAME, DEFAULT_NAME),
            )

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=2,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        LOGGER.warning("Failed to unload platforms for %s", entry.entry_id)
        return False

    # Remove from hass.data
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated (z.B. polling interval)."""
    LOGGER.debug("Reloading PowerOcean entry %s due to options change", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
