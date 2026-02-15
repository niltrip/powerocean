"""
Collector module for PowerOcean integration.

Defines interfaces and concrete collectors for parsing EcoFlow API responses:
- `ReportCollector`: Protocol for generic collection of sensor datapoints.
- `StructureCollector`: Collects device structure and metadata without runtime values.
- `ValueCollector`: Collects live sensor values only.
"""

from typing import Any, Protocol

from homeassistant.helpers.device_registry import DeviceInfo

from .types import PowerOceanEndPoint, SensorMetaHelper


class ReportCollector(Protocol):
    """
    Common interface for all report collectors.

    Any collector must implement the `collect` method that receives parsed
    sensor data points.
    """

    def collect(
        self,
        *,
        unique_id: str,
        device_sn: str,
        key: str,
        value: Any,
        device_info: DeviceInfo | None,
        name: str,
        friendly_name: str,
    ) -> None:
        """Collect a parsed sensor datapoint."""


class StructureCollector:
    """
    Collects device structure and endpoint metadata.

    Stores information about sensors, their descriptions, classes, icons,
    and associated device information. Values are set to None since this
    collector focuses on structure only.
    """

    def __init__(self) -> None:
        """Initialize the StructureCollector with an empty endpoint dictionary."""
        self.endpoints: dict[str, PowerOceanEndPoint] = {}

    def collect(
        self,
        unique_id: str,
        device_sn: str,
        key: str,
        value: Any,
        device_info: DeviceInfo | None,
        name: str,
        friendly_name: str,
    ) -> None:
        """Collect a parsed sensor datapoint."""
        if unique_id in self.endpoints:
            return

        self.endpoints[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=device_sn,
            name=name,
            friendly_name=friendly_name,
            value=None,  # Struktur → kein Wert
            cls=SensorMetaHelper.get_class(key),
            description=SensorMetaHelper.get_description(key),
            icon=SensorMetaHelper.get_special_icon(key),
            device_info=device_info,
        )


class ValueCollector:
    """
    Collects live sensor values only.

    Stores primitive sensor values (int, float, str) keyed by unique ID.
    Ignores non-primitive types like dict or list.
    """

    def __init__(self) -> None:
        """Initialize the ValueCollector with an empty values dictionary."""
        self.values: dict[str, float | int | str] = {}

    def collect(
        self,
        unique_id: str,
        device_sn: str,
        key: str,
        value: Any,
        device_info: DeviceInfo | None,
        name: str,
        friendly_name: str,
    ) -> None:
        """Collect a parsed sensor datapoint."""
        if not isinstance(value, (int, float, str)):
            return

        self.values[unique_id] = value
