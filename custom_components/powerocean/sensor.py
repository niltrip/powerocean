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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import PORTAL_URL
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .types import DISPLAY_PRECISION, PowerOceanEndPoint

# Updates are fetched centrally by the coordinator
PARALLEL_UPDATES = 0

# Cause and solution of error codes are only available in the portal
ERROR_DETAILS_HINT = f"Ursache und Lösung im EcoFlow-Webportal: {PORTAL_URL}"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
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
        async_add_entities(
            [PowerOceanSensor(coordinator, endpoint) for endpoint in new]
        )

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PowerOceanSensor(CoordinatorEntity[PowerOceanCoordinator], SensorEntity):
    """Representation of a PowerOcean Sensor using DataUpdateCoordinator."""

    def __init__(
        self, coordinator: PowerOceanCoordinator, endpoint: PowerOceanEndPoint
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.endpoint = endpoint
        self._endpoint_id = endpoint.internal_unique_id
        self._endpoint_friendly_name = endpoint.friendly_name
        self._endpoint_icon = endpoint.icon
        self._endpoint_description = endpoint.description
        self._endpoint_serial = endpoint.serial
        self._endpoint_device_info = endpoint.device_info

        (
            self._attr_device_class,
            self._attr_native_unit_of_measurement,
            self._attr_state_class,
        ) = getattr(endpoint, "cls", None) or (None, None, None)

        # HA attributes
        self._attr_has_entity_name = True
        self._attr_unique_id = self._endpoint_id
        # Without a translated name, the key is the name
        if not endpoint.named:
            self._attr_name = self._endpoint_friendly_name
        self._attr_icon = self._endpoint_icon
        self._attr_entity_registry_enabled_default = endpoint.enabled_default
        # Translated name and states
        self._attr_translation_key = endpoint.translation_key
        self._translated_states = endpoint.translated_states
        self._attr_suggested_display_precision = DISPLAY_PRECISION.get(
            self._attr_native_unit_of_measurement
        )
        if self._attr_native_unit_of_measurement is None or endpoint.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def suggested_object_id(self) -> str | None:
        """Return the key, so entity IDs do not depend on the name."""
        return self._endpoint_friendly_name

    @property
    def available(self) -> bool:
        """Return False while the response lacks the value (e.g. battery offline)."""
        return super().available and self._endpoint_id in self.coordinator.data

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor from coordinator."""
        value = self.coordinator.data.get(self._endpoint_id)
        # Translated states must be lowercase: WORKMODE_SELFUSE, True
        if self._translated_states and value is not None:
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
        if self._endpoint_description:
            attr["product_description"] = self._endpoint_description
        if self._endpoint_serial:
            attr["product_serial"] = self._endpoint_serial
        errors = self.coordinator.active_errors.get(self._endpoint_id)
        if errors is not None:
            attr["errors"] = [error.as_dict() for error in errors]
            if errors:
                attr["error_details"] = ERROR_DETAILS_HINT
        return attr

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info for Home Assistant."""
        return self._endpoint_device_info
