"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import (
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import PORTAL_URL
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .types import DISPLAY_PRECISION, SUGGESTED_UNITS, PowerOceanEndPoint

# Updates are fetched centrally by the coordinator
PARALLEL_UPDATES = 0

# Cause and solution of error codes are only available in the portal
ERROR_DETAILS_HINT = f"Ursache und Lösung im EcoFlow-Webportal: {PORTAL_URL}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """
    Set up PowerOcean sensors for a config entry.

    This function is called by Home Assistant when a config entry
    is set up. It creates sensor entities for each endpoint and
    registers them with Home Assistant. Endpoints that appear with a
    later update (e.g. a battery that was offline) are added then, as are
    the endpoints of a removed device that returns.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry to set up.
        async_add_entities: Callback to add entities to Home Assistant.

    Returns:
        None

    """
    coordinator = entry.runtime_data
    # Endpoint each entity was added for. Removing a device drops its
    # endpoints; if the device returns, the new endpoints are added again.
    added: dict[str, PowerOceanEndPoint] = {}

    @callback
    def _add_new_entities() -> None:
        new = [
            endpoint
            for unique_id, endpoint in coordinator.endpoints.items()
            if added.get(unique_id) is not endpoint
        ]
        if not new:
            return
        added.update((endpoint.internal_unique_id, endpoint) for endpoint in new)
        _register_devices(hass, coordinator, new)
        async_add_entities(
            [PowerOceanSensor(coordinator, endpoint) for endpoint in new]
        )

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


@callback
def _register_devices(
    hass: HomeAssistant,
    coordinator: PowerOceanCoordinator,
    endpoints: list[PowerOceanEndPoint],
) -> None:
    """
    Register the devices of the endpoints and link them to their parent device.

    `via_device` in the device info is deprecated since Home Assistant 2026.10
    and its replacement `via_device_id` is missing there before 2026.10. So the
    devices are created first and then linked with async_update_device, which
    takes `via_device_id` in all supported versions.
    """
    device_registry = dr.async_get(hass)
    entry_id = coordinator.config_entry.entry_id
    parents: dict[str, str] = {}
    for endpoint in endpoints:
        if endpoint.device_info and endpoint.serial not in parents:
            device_registry.async_get_or_create(
                config_entry_id=entry_id, **endpoint.device_info
            )
            if endpoint.via_serial:
                parents[endpoint.serial] = endpoint.via_serial
    devices = coordinator.devices(device_registry)
    for sn, via_sn in parents.items():
        device, parent = devices.get(sn), devices.get(via_sn)
        if device and parent and device.via_device_id != parent.id:
            device_registry.async_update_device(device.id, via_device_id=parent.id)


class PowerOceanSensor(CoordinatorEntity[PowerOceanCoordinator], SensorEntity):
    """Representation of a PowerOcean Sensor using DataUpdateCoordinator."""

    def __init__(
        self, coordinator: PowerOceanCoordinator, endpoint: PowerOceanEndPoint
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.endpoint = endpoint
        (
            self._attr_device_class,
            self._attr_native_unit_of_measurement,
            self._attr_state_class,
        ) = endpoint.cls or (None, None, None)

        # HA attributes
        self._attr_has_entity_name = True
        self._attr_unique_id = endpoint.internal_unique_id
        self._attr_device_info = endpoint.device_info
        # Without a translated name, the key is the name
        if not endpoint.named:
            self._attr_name = endpoint.friendly_name
        self._attr_icon = endpoint.icon
        self._attr_entity_registry_enabled_default = endpoint.enabled_default
        # Translated name and states
        self._attr_translation_key = endpoint.translation_key
        self._attr_suggested_display_precision = DISPLAY_PRECISION.get(
            self._attr_native_unit_of_measurement
        )
        if suggested_unit := SUGGESTED_UNITS.get(endpoint.friendly_name):
            self._attr_suggested_unit_of_measurement = suggested_unit
        if self._attr_native_unit_of_measurement is None or endpoint.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def suggested_object_id(self) -> str | None:
        """Return the key, so entity IDs do not depend on the name."""
        return self.endpoint.friendly_name

    @property
    def available(self) -> bool:
        """Return False while the response lacks the value (e.g. battery offline)."""
        return super().available and self.unique_id in self.coordinator.data

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor from coordinator."""
        value = self.coordinator.data.get(self.unique_id)
        # Translated states must be lowercase: WORKMODE_SELFUSE, True
        if self.endpoint.translated_states and value is not None:
            return str(value).lower()
        # Times are parsed as Unix time
        if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
            # 0 means no time (e.g. no charging order), not 1970
            if not isinstance(value, (int, float)) or value <= 0:
                return None
            try:
                return datetime.fromtimestamp(value, UTC)
            except (ValueError, OverflowError, OSError):
                return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for this sensor."""
        attr = {}
        if self.endpoint.description:
            attr["product_description"] = self.endpoint.description
        if self.endpoint.serial:
            attr["product_serial"] = self.endpoint.serial
        errors = self.coordinator.active_errors.get(self.unique_id)
        if errors is not None:
            attr["errors"] = [error.as_dict() for error in errors]
            if errors:
                attr["error_details"] = ERROR_DETAILS_HINT
        return attr
