"""
PowerOcean integration for Home Assistant.

This module sets up the PowerOcean integration, handling:

- Authentication and device setup via EcoFlow API.
- Migration of old config entries to new structure.
- Creation of a DataUpdateCoordinator for periodic polling.
- Registration in the Home Assistant device registry.
- Forwarding entry setups to sensor/platform components.

Functions:
- async_setup_entry: Set up a PowerOcean device from a config entry.
- async_migrate_entry: Migrate older config entries to the latest version.
- async_unload_entry: Unload a config entry and clean up resources.
"""

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr

from .const import (
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PLATFORMS,
    PowerOceanModel,
)
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .ecoflow import HAEcoflowApi


async def async_setup_entry(hass: HomeAssistant, entry: PowerOceanConfigEntry) -> bool:
    """Set up PowerOcean from a config entry."""
    device_id = entry.data.get(CONF_DEVICE_ID)
    model_id = entry.data.get(CONF_MODEL_ID)
    if not device_id or not model_id:
        # Kann sich nicht von selbst beheben → kein Retry
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="missing_configuration"
        )

    api = HAEcoflowApi(
        hass,
        device_id,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        model_id,
    )

    # Login und erster Abruf. Netzwerkfehler führen zum Retry des Setups,
    # abgelehnte Zugangsdaten starten den Reauth-Flow.
    coordinator = PowerOceanCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # --- Sensor-Plattformen laden ---
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    model_name = MODEL_NAME_MAP[PowerOceanModel(model_id)]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_id)},
        manufacturer="EcoFlow",
        serial_number=device_id,
        name=entry.options.get(CONF_FRIENDLY_NAME),
        model=model_name,
        model_id=model_id,
        configuration_url="https://user-portal.ecoflow.com/",
    )

    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: PowerOceanConfigEntry
) -> bool:
    """Migrate old config entries to new structure."""
    if entry.version > 2:
        # Downgrade von einer neueren Version wird nicht unterstützt
        return False

    data = dict(entry.data)
    options = dict(entry.options)

    if entry.version < 2 and "user_input" in data:
        # ALT → NEU
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

    if entry.version < 2 or entry.minor_version < 2:
        # 2.2: fehlende Optionen ergänzen (vorher bei jedem Setup erledigt)
        legacy_options = data.get("options", {})
        options.setdefault(
            CONF_SCAN_INTERVAL,
            legacy_options.get("scan_interval", DEFAULT_SCAN_INTERVAL),
        )
        options.setdefault(
            CONF_FRIENDLY_NAME,
            legacy_options.get("friendly_name", DEFAULT_NAME),
        )

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
        minor_version=2,
    )
    LOGGER.debug(
        "Migrated %s to version %s.%s", entry.title, entry.version, entry.minor_version
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PowerOceanConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
