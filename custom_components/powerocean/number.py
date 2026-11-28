"""
PowerOcean number entities: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": the LED brightness of the
inverter, the backup reserve, the surplus solar energy delivery and the
power of the charging windows, set as the EcoFlow app does.
"""

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .charge_window import ChargeWindowEntity, setup_charge_window_entities
from .const import CONF_MQTT, DOMAIN
from .coordinator import (
    CHARGE_POWER_STEP,
    SOC_LIMITS,
    PowerOceanConfigEntry,
    PowerOceanCoordinator,
    charge_power_max,
)

# One command at a time, each waits for its acknowledgement
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the settings, if the MQTT option is on."""
    coordinator = entry.runtime_data
    mqtt = entry.options.get(CONF_MQTT, False)
    settings = [
        (PowerOceanLedBrightness(coordinator), mqtt),
        (PowerOceanBackupReserve(coordinator), mqtt),
        (PowerOceanSolarSurplus(coordinator), mqtt),
    ]
    async_add_entities(setting for setting, wanted in settings if wanted)
    # Switched off: remove the sliders instead of leaving them
    # unavailable
    entity_registry = er.async_get(hass)
    for setting, wanted in settings:
        if not wanted and (
            entity_id := entity_registry.async_get_entity_id(
                NUMBER_DOMAIN, DOMAIN, setting.unique_id
            )
        ):
            entity_registry.async_remove(entity_id)
    setup_charge_window_entities(
        hass,
        entry,
        async_add_entities,
        NUMBER_DOMAIN,
        lambda coordinator, index: [PowerOceanChargeWindowPower(coordinator, index)],
    )


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


class PowerOceanSocLimit(PowerOceanSetting):
    """A setting sent in one command with the other SOC_LIMITS, in steps of 1 %."""

    _attr_native_step = 1

    @property
    def available(self) -> bool:
        """Return False also without the other values, which are sent along."""
        return super().available and all(
            name in self.coordinator.soc_limits for name in SOC_LIMITS
        )


class PowerOceanBackupReserve(PowerOceanSocLimit):
    """Backup reserve of the battery in % (sysBatDsgDownLimit)."""

    _attr_translation_key = "backup_reserve"
    _datapoint = "sysBatDsgDownLimit"

    async def async_set_native_value(self, value: float) -> None:
        """Send the new backup reserve; raises if the system does not confirm it."""
        await self.coordinator.async_set_backup_reserve(int(value))


class PowerOceanSolarSurplus(PowerOceanSocLimit):
    """Surplus solar energy delivery of the app in % (devSoc)."""

    _attr_translation_key = "solar_surplus"
    _datapoint = "devSoc"

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value; raises if the system does not confirm it."""
        await self.coordinator.async_set_solar_surplus(int(value))


class PowerOceanChargeWindowPower(ChargeWindowEntity, NumberEntity):
    """Charging power of a charging window of the app in W (issue #213)."""

    _attr_translation_key = "charge_window_power"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = CHARGE_POWER_STEP
    _attr_native_step = CHARGE_POWER_STEP

    def __init__(self, coordinator: PowerOceanCoordinator, task_index: int) -> None:
        """Initialize the power of the window with this taskIndex."""
        super().__init__(coordinator, task_index, "_power")
        self._attr_native_max_value = charge_power_max(coordinator.api.sn)

    @property
    def native_value(self) -> int | None:
        """Return the power the system reported last."""
        task = self.task
        return None if task is None else task.power

    async def async_set_native_value(self, value: float) -> None:
        """Send the new power; raises if the system does not confirm it."""
        await self.coordinator.async_set_charge_task(self.task_index, power=int(value))
