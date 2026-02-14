"""tests/utils."""

from enum import Enum
from typing import Any

from custom_components.powerocean.types import PowerOceanEndPoint


def serialize_sensors(sensors: dict[str, PowerOceanEndPoint]) -> dict:
    """
    Convert EndPoint objects into a JSON-serializable dict for Golden Master testing.

    Args:
        sensors: dict of PowerOceanEndPoint objects keyed by unique_id.

    Returns:
        dict: Flattened, serializable dict of sensors.

    """
    serialized = {}
    for uid, sensor in sensors.items():
        serialized[uid] = {
            "internal_unique_id": getattr(sensor, "internal_unique_id", ""),
            "name": getattr(sensor, "name", ""),
            "friendly_name": getattr(sensor, "friendly_name", ""),
            "value": getattr(sensor, "value", None),
            "unit": (getattr(sensor, "cls", None) or (None, None, None))[1],
            "description": getattr(sensor, "description", ""),
            "icon": getattr(sensor, "icon", None),
        }
    # Sort keys to ensure deterministic order
    return dict(sorted(serialized.items(), key=lambda x: x[0]))


def normalize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [normalize(v) for v in obj]
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    return obj
