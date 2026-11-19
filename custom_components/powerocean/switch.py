"""
PowerOcean switches: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": the battery save mode
and the battery preheating, switched as the EcoFlow app does. The battery
save mode not on models whose command is unknown (PowerOcean Plus).
"""

from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
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
    """Set up the switches, if the MQTT option is on and the command known."""
    coordinator = entry.runtime_data
    mqtt = entry.options.get(CONF_MQTT, False)
    switches: list[tuple[SwitchEntity, bool]] = [
        (PowerOceanBatterySaveMode(coordinator), mqtt and coordinator.settings_known),
        (PowerOceanBatteryPreheat(coordinator), mqtt),
    ]
    async_add_entities(switch for switch, wanted in switches if wanted)
    # Switched off or unknown: remove the switches instead of leaving them
    # unavailable
    entity_registry = er.async_get(hass)
    for switch, wanted in switches:
        if not wanted and (
            entity_id := entity_registry.async_get_entity_id(
                SWITCH_DOMAIN, DOMAIN, switch.unique_id
            )
        ):
            entity_registry.async_remove(entity_id)


class PowerOceanBatterySaveMode(CoordinatorEntity[PowerOceanCoordinator], SwitchEntity):
    """Battery save mode of the app, only reported via MQTT."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "battery_save_mode"

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the switch of the inverter."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self._attr_unique_id = f"{sn}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def available(self) -> bool:
        """Return False without MQTT connection, state or sysZone (sent along)."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.is_on is not None
            and self.coordinator.setting("sysZone") is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the state the system reported last."""
        value = self.coordinator.setting("batterySaveMode")
        return None if value is None else bool(value)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_battery_save_mode(enabled=False)


class PowerOceanBatteryPreheat(CoordinatorEntity[PowerOceanCoordinator], SwitchEntity):
    """Battery preheating of the app, from the heating task report."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "battery_preheat"

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the switch of the inverter."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self._attr_unique_id = f"{sn}_{self._attr_translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or without the task (sent along)."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.coordinator.preheat is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the state the system reported last."""
        task = self.coordinator.preheat
        return None if task is None else task.enabled

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Switch on; raises if the system does not confirm it."""
        await self.coordinator.async_set_preheat(enabled=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Switch off; raises if the system does not confirm it."""
        await self.coordinator.async_set_preheat(enabled=False)
