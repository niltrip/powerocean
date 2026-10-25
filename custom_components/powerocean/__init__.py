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
- async_remove_config_entry_device: Allow removing devices that are gone.
- async_unload_entry: Unload a config entry and clean up resources.
"""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
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
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_MQTT,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PLATFORMS,
    PORTAL_URL,
    PowerOceanModel,
)
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .datapoints import EDEV_SYSTEM_KEYS, SN_KEYS
from .ecoflow import HAEcoflowApi

if TYPE_CHECKING:
    from collections.abc import Callable


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
        configuration_url=PORTAL_URL,
    )

    # Optional: firmware of the modules via MQTT. Failures do not stop the
    # setup, the values come from HTTP.
    if entry.options.get(CONF_MQTT, False):
        entry.async_on_unload(coordinator.async_stop_mqtt)
        await coordinator.async_start_mqtt()

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
        # 1.x: Zugangsdaten unter data["user_input"], Name und Intervall
        # unter data["options"] (siehe unten)
        old = data.pop("user_input")

        data.update(
            {
                CONF_DEVICE_ID: old.get(CONF_DEVICE_ID),
                CONF_EMAIL: old.get(CONF_EMAIL),
                CONF_PASSWORD: old.get(CONF_PASSWORD),
                CONF_MODEL_ID: old.get(CONF_MODEL_ID),
            }
        )

    if entry.version < 2 or entry.minor_version < 2:
        # 1.x und 2.1: Optionen aus data["options"] übernehmen und die
        # Altlasten aus data entfernen
        legacy_options = data.pop("options", None) or {}
        data.pop("device_info", None)
        legacy_name = legacy_options.get(CONF_FRIENDLY_NAME)

        options.setdefault(
            CONF_SCAN_INTERVAL,
            legacy_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        # Die Migration auf 2.1 hat den Namen aus data["options"] übersehen
        # und den Standardnamen gesetzt. Einen eigenen Namen nicht anfassen.
        if legacy_name and options.get(CONF_FRIENDLY_NAME) in (None, DEFAULT_NAME):
            options[CONF_FRIENDLY_NAME] = legacy_name
        options.setdefault(CONF_FRIENDLY_NAME, DEFAULT_NAME)

    # Entities that are no longer provided, by the version that dropped them
    inverter = data.get(CONF_DEVICE_ID) or ""
    obsolete: list[tuple[int, Callable[[str], bool]]] = [
        # 2.3: Die Batterie-Firmware steht jetzt auf der Geräteseite
        (3, lambda unique_id: unique_id.endswith("_moduleAplSwVer")),
        # 2.4: Die Anlagenwerte aus EDEV_SYS_REPORT hingen an der Wallbox,
        # jetzt am Wechselrichter (neue unique_id)
        (4, lambda unique_id: _is_edev_system_value_of_device(unique_id, inverter)),
        # 2.5: Die Seriennummern stehen schon in der Geräteinfo
        (5, lambda unique_id: unique_id.rpartition("_")[2] in SN_KEYS),
    ]
    removed = [
        is_obsolete
        for minor, is_obsolete in obsolete
        if entry.version < 2 or entry.minor_version < minor
    ]
    if removed:
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if any(is_obsolete(entity.unique_id) for is_obsolete in removed):
                entity_registry.async_remove(entity.entity_id)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
        minor_version=5,
    )
    LOGGER.debug(
        "Migrated %s to version %s.%s", entry.title, entry.version, entry.minor_version
    )
    return True


def _is_edev_system_value_of_device(unique_id: str, inverter: str) -> bool:
    """Return True for a system value of EDEV_SYS_REPORT on another device."""
    # unique_id = <Seriennummer>_<Präfix>_EDEV_SYS_REPORT_<Schlüssel>
    head, found, key = unique_id.rpartition("_EDEV_SYS_REPORT_")
    return bool(found) and key in EDEV_SYSTEM_KEYS and not head.startswith(inverter)


async def async_unload_entry(hass: HomeAssistant, entry: PowerOceanConfigEntry) -> bool:
    """Unload a config entry and clean up resources."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removing a device that has no values in the last response."""
    if entry.state is not ConfigEntryState.LOADED:
        # Without a response it is unknown which devices are present
        return False
    coordinator = entry.runtime_data
    present = {
        coordinator.endpoints[unique_id].serial
        for unique_id in coordinator.data
        if unique_id in coordinator.endpoints
    }
    serials = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    if serials & present:
        return False
    # Its entities are added again if the device returns
    for unique_id, endpoint in list(coordinator.endpoints.items()):
        if endpoint.serial in serials:
            del coordinator.endpoints[unique_id]
    return True
