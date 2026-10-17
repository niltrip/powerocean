"""
PowerOcean integration for Home Assistant.

This module sets up the PowerOcean integration, handling:

- Initialization of the integration.
- Authentication and device setup via EcoFlow API.
- Migration of old config entries to new structure.
- Creation of a DataUpdateCoordinator for periodic polling.
- Registration in the Home Assistant device registry.
- Forwarding entry setups to sensor/platform components.

Functions:
- async_setup: Set up the integration and log basic info.
- async_setup_entry: Set up a PowerOcean device from a config entry.
- async_migrate_entry: Migrate older config entries to the latest version.
- async_unload_entry: Unload a config entry and clean up resources.
"""

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
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import AuthenticationError, EcoflowApiError
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
from .ecoflow import HAEcoflowApi
from .parser import EcoflowParser


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
        name = manifest.get("name", DOMAIN)
        version = manifest.get("version", "unknown")
        requirements = manifest.get("requirements", [])

        LOGGER.debug(
            "Loading %s v%s (requirements: %s)",
            name,
            version,
            requirements,
        )

    except KeyError as err:
        LOGGER.error("Missing expected key in integration manifest: %s", err)
        return False
    except HomeAssistantError as err:
        LOGGER.error("Home Assistant error during PowerOcean setup: %s", err)
        return False
    except Exception:  # optional fallback, nur loggen
        LOGGER.exception("Unexpected error loading PowerOcean integration")
        raise  # Fehler weiterwerfen, damit HA korrekt reagiert

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    options = dict(entry.options)
    updated = False

    # Migration fehlender Optionen
    if CONF_SCAN_INTERVAL not in options:
        options[CONF_SCAN_INTERVAL] = entry.data.get("options", {}).get(
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

    # --- EcoFlow API initialisieren ---
    device_id = entry.data.get(CONF_DEVICE_ID)
    model_id = entry.data.get(CONF_MODEL_ID)
    if not device_id or not model_id:
        msg = "Missing device_id or model_id in config entry"
        raise ConfigEntryNotReady(msg)

    api = HAEcoflowApi(
        hass,
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model_id,
    )

    # --- Authentifizieren ---
    try:
        await api.async_authorize()
    except AuthenticationError as err:
        # Zugangsdaten abgelehnt → HA startet den Reauth-Flow
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except EcoflowApiError as err:
        # Netzwerk-/Cloudproblem → HA wiederholt das Setup automatisch
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    # --- Struktur & Parser ---
    raw = await api.fetch_raw()
    parser = EcoflowParser(variant=api.variant, sn=api.sn)
    endpoints = parser.parse_structure(raw)

    # --- DataUpdateCoordinator ---
    scan_interval = timedelta(
        seconds=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    coordinator = PowerOceanCoordinator(
        hass=hass, api=api, update_interval=scan_interval
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "endpoints": endpoints,
    }

    # --- Sensor-Plattformen laden ---
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    model_name = MODEL_NAME_MAP[PowerOceanModel(model_id)]
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

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to new structure."""
    version = entry.version
    data = dict(entry.data)
    options = dict(entry.options)

    if version < 2:
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
        version = 2

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=version,
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
