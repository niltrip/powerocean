"""
Charging windows of the EcoFlow app as entities (issue #213).

Grid charging at set times with a set power: a switch and a power setting
for each window set up in the app. Windows can be added in the app later,
so the entities are added when a window first appears. Only with the option
"Extended connection (MQTT)".
"""

from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MQTT, DOMAIN
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator, format_minutes
from .mqtt import ChargeTask

# Start of the unique_id of all charging window entities, after the serial
UNIQUE_ID_PART = "charge_window_"


def charge_window_unique_id(sn: str, task_index: int, suffix: str = "") -> str:
    """Return the unique_id of an entity of a charging window."""
    return f"{sn}_{UNIQUE_ID_PART}{task_index}{suffix}"


def format_windows(task: ChargeTask) -> str:
    """Return the time windows of a task as HH:MM-HH:MM, separated by commas."""
    return ", ".join(
        f"{format_minutes(start)}-{format_minutes(end)}" for start, end in task.windows
    )


class ChargeWindowEntity(CoordinatorEntity[PowerOceanCoordinator], Entity):
    """An entity of one charging window, on the inverter."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: PowerOceanCoordinator, task_index: int, suffix: str = ""
    ) -> None:
        """Initialize the entity of the window with this taskIndex."""
        super().__init__(coordinator)
        sn = coordinator.api.sn
        self.task_index = task_index
        self._attr_unique_id = charge_window_unique_id(sn, task_index, suffix)
        self._attr_translation_placeholders = {"index": str(task_index)}
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def task(self) -> ChargeTask | None:
        """Return the window, None if the cloud does not report it (deleted)."""
        return self.coordinator.charge_tasks.get(self.task_index)

    @property
    def available(self) -> bool:
        """Return False without MQTT connection or without the window."""
        return (
            super().available
            and self.coordinator.mqtt is not None
            and self.task is not None
        )


def setup_charge_window_entities(
    hass: HomeAssistant,
    entry: PowerOceanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    domain: str,
    create: Callable[[PowerOceanCoordinator, int], list[Entity]],
) -> None:
    """
    Add the entities of each charging window, also of windows that come later.

    Without the MQTT option, remove them instead of leaving them unavailable.
    """
    coordinator = entry.runtime_data
    if not entry.options.get(CONF_MQTT, False):
        entity_registry = er.async_get(hass)
        prefix = f"{coordinator.api.sn}_{UNIQUE_ID_PART}"
        for entity in er.async_entries_for_config_entry(
            entity_registry, entry.entry_id
        ):
            if entity.domain == domain and entity.unique_id.startswith(prefix):
                entity_registry.async_remove(entity.entity_id)
        return
    added: set[int] = set()

    @callback
    def _add_new_windows() -> None:
        new = sorted(set(coordinator.charge_tasks) - added)
        if not new:
            return
        added.update(new)
        async_add_entities(
            [entity for index in new for entity in create(coordinator, index)]
        )

    _add_new_windows()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_windows))
