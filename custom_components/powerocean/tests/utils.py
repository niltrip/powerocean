# tests/utils.py
from custom_components.powerocean.ecoflow import PowerOceanEndPoint


def serialize_sensors(sensors: dict[str, PowerOceanEndPoint]) -> dict:
    """
    Convert PowerOceanEndPoint objects into a JSON-serializable dict for Golden Master testing.

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
            "unit": getattr(sensor, "unit", ""),
            "description": getattr(sensor, "description", ""),
            "icon": getattr(sensor, "icon", None),
        }
    # Sort keys to ensure deterministic order
    return dict(sorted(serialized.items(), key=lambda x: x[0]))
