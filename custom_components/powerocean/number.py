"""
PowerOcean number entities: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": the LED brightness of the
inverter, set as the EcoFlow app does.
"""

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MQTT, DOMAIN
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator

# One command at a time, each waits for its acknowledgement
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the LED brightness, if the MQTT option is on."""
    coordinator = entry.runtime_data
    if entry.options.get(CONF_MQTT, False):
        async_add_entities([PowerOceanLedBrightness(coordinator)])
        return
    # Switched off: remove the slider instead of leaving it unavailable
    entity_registry = er.async_get(hass)
    if entity_id := entity_registry.async_get_entity_id(
        NUMBER_DOMAIN, DOMAIN, _led_unique_id(coordinator)
    ):
        entity_registry.async_remove(entity_id)


def _led_unique_id(coordinator: PowerOceanCoordinator) -> str:
    return f"{coordinator.api.sn}_led_brightness"


class PowerOceanLedBrightness(CoordinatorEntity[PowerOceanCoordinator], NumberEntity):
    """LED brightness of the inverter in %, as in the EcoFlow app."""

    _attr_has_entity_name = True
    _attr_translation_key = "led_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    # The app offers steps of 10 %
    _attr_native_step = 10
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the LED brightness of the inverter."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self._attr_unique_id = _led_unique_id(coordinator)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or without a current value."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.native_value is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the LED brightness from the last response."""
        key = self.coordinator.led_brightness_key
        value = self.coordinator.data.get(key) if key else None
        return value if isinstance(value, int | float) else None

    async def async_set_native_value(self, value: float) -> None:
        """Send the new brightness; raises if the system does not confirm it."""
        await self.coordinator.async_set_led_brightness(int(value))
