"""
PowerOcean select entities: settings sent to the system via MQTT.

Only with the option "Extended connection (MQTT)": start and end of the
battery preheating, in steps of 30 minutes as in the EcoFlow app.
"""

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MQTT, DOMAIN
from .coordinator import (
    MINUTES_PER_DAY,
    PowerOceanConfigEntry,
    PowerOceanCoordinator,
    format_minutes,
)
from .mqtt import PreheatTask

# One command at a time, each waits for its acknowledgement
PARALLEL_UPDATES = 1

# The app offers the times in steps of 30 minutes
STEP = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects, if the MQTT option is on."""
    coordinator = entry.runtime_data
    selects: list[SelectEntity] = [
        PowerOceanPreheatStart(coordinator),
        PowerOceanPreheatEnd(coordinator),
    ]
    if entry.options.get(CONF_MQTT, False):
        async_add_entities(selects)
        return
    # Switched off: remove the selects instead of leaving them unavailable
    entity_registry = er.async_get(hass)
    for select in selects:
        if entity_id := entity_registry.async_get_entity_id(
            SELECT_DOMAIN, DOMAIN, select.unique_id
        ):
            entity_registry.async_remove(entity_id)


class PowerOceanPreheatTime(CoordinatorEntity[PowerOceanCoordinator], SelectEntity):
    """Start or end of the battery preheating, as HH:MM."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # First and last time offered, in minutes after midnight
    _first: int
    _last: int

    def __init__(self, coordinator: PowerOceanCoordinator) -> None:
        """Initialize the select of the inverter."""
        super().__init__(coordinator)
        self._attr_options = [
            format_minutes(m) for m in range(self._first, self._last + 1, STEP)
        ]
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
    def current_option(self) -> str | None:
        """Return the time the system reported last."""
        task = self.coordinator.preheat
        return None if task is None else format_minutes(self._minutes(task))

    def _minutes(self, task: PreheatTask) -> int:
        raise NotImplementedError


def _minutes_of(option: str) -> int:
    hours, minutes = option.split(":")
    return int(hours) * 60 + int(minutes)


class PowerOceanPreheatStart(PowerOceanPreheatTime):
    """Start of the battery preheating: 00:00 to 23:30."""

    _attr_translation_key = "battery_preheat_start"
    _first = 0
    _last = MINUTES_PER_DAY - STEP

    def _minutes(self, task: PreheatTask) -> int:
        return task.start

    async def async_select_option(self, option: str) -> None:
        """Send the new start; raises if it is not before the end."""
        await self.coordinator.async_set_preheat(start=_minutes_of(option))


class PowerOceanPreheatEnd(PowerOceanPreheatTime):
    """End of the battery preheating: 00:30 to 24:00."""

    _attr_translation_key = "battery_preheat_end"
    _first = STEP
    _last = MINUTES_PER_DAY

    def _minutes(self, task: PreheatTask) -> int:
        return task.end

    async def async_select_option(self, option: str) -> None:
        """Send the new end; raises if it is not after the start."""
        await self.coordinator.async_set_preheat(end=_minutes_of(option))
