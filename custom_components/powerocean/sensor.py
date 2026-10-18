"""
PowerOcean sensor integration for Home Assistant.

This module defines the setup and management of PowerOcean sensor entities,
including data fetching, entity registration, and periodic updates.
"""

from typing import Any

from homeassistant.components.sensor import (
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

from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .types import PowerOceanEndPoint

# Updates are fetched centrally by the coordinator
PARALLEL_UPDATES = 0

# Cause and solution of error codes are only available in the portal
ERROR_DETAILS_HINT = (
    "Ursache und Lösung im EcoFlow-Webportal: https://portal.ecoflow.com"
)


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
    later update (e.g. a battery that was offline) are added then.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry to set up.
        async_add_entities: Callback to add entities to Home Assistant.

    Returns:
        None

    """
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new = [
            endpoint
            for unique_id, endpoint in coordinator.endpoints.items()
            if unique_id not in added
        ]
        if not new:
            return
        added.update(endpoint.internal_unique_id for endpoint in new)
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
        self._endpoint_name = endpoint.name
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
        self._attr_name = self._endpoint_friendly_name
        self._attr_icon = self._endpoint_icon
        if self._attr_native_unit_of_measurement is None:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor from coordinator."""
        return self.coordinator.data.get(self._endpoint_id)

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
