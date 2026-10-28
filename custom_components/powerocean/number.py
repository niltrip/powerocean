"""
PowerOcean number entities: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": the LED brightness of the
inverter and the backup reserve, set as the EcoFlow app does.
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
    """Set up the settings, if the MQTT option is on."""
    coordinator = entry.runtime_data
    settings = [
        PowerOceanLedBrightness(coordinator),
        PowerOceanBackupReserve(coordinator),
    ]
    if entry.options.get(CONF_MQTT, False):
        async_add_entities(settings)
        return
    # Switched off: remove the sliders instead of leaving them unavailable
    entity_registry = er.async_get(hass)
    for setting in settings:
        if entity_id := entity_registry.async_get_entity_id(
            NUMBER_DOMAIN, DOMAIN, setting.unique_id
        ):
            entity_registry.async_remove(entity_id)


class PowerOceanSetting(CoordinatorEntity[PowerOceanCoordinator], NumberEntity):
    """A setting of the inverter in %, as in the EcoFlow app."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_unit_of_measurement = PERCENTAGE
    # Datapoint that holds the current value
    _datapoint: str

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the setting of the inverter."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self._attr_unique_id = f"{sn}_{self._attr_translation_key}"
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
    def native_value(self) -> int | None:
        """Return the current value."""
        return self.coordinator.setting(self._datapoint)


class PowerOceanLedBrightness(PowerOceanSetting):
    """LED brightness of the inverter in %."""

    _attr_translation_key = "led_brightness"
    # The app offers steps of 10 %
    _attr_native_step = 10
    _datapoint = "emsCtrlLedBright"

    async def async_set_native_value(self, value: float) -> None:
        """Send the new brightness; raises if the system does not confirm it."""
        await self.coordinator.async_set_led_brightness(int(value))


class PowerOceanBackupReserve(PowerOceanSetting):
    """Backup reserve of the battery in % (sysBatDsgDownLimit)."""

    _attr_translation_key = "backup_reserve"
    _attr_native_step = 1
    _datapoint = "sysBatDsgDownLimit"

    @property
    def available(self) -> bool:
        """Return False also without charge limit or devSoc, which are sent along."""
        return super().available and all(
            name in self.coordinator.soc_limits
            for name in ("sysBatChgUpLimit", "devSoc")
        )

    async def async_set_native_value(self, value: float) -> None:
        """Send the new backup reserve; raises if the system does not confirm it."""
        await self.coordinator.async_set_backup_reserve(int(value))
