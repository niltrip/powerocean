from __future__ import annotations

from typing import Any

from custom_components.powerocean.ecoflow import PowerOceanEndPoint


def serialize_structure(endpoints: dict[str, Any]) -> dict:
    """
    Serialize PowerOcean structure for golden-master testing.

    - No values
    - No Home Assistant objects
    - Stable ordering
    """
    result: dict[str, dict] = {}

    for device_sn, endpoint in endpoints.items():
        result[device_sn] = {
            "device": _serialize_device(endpoint),
            "sensors": serialize_endpoint(endpoint),
        }

    return dict(sorted(result.items()))


def _serialize_device(endpoint) -> dict:
    device_info = getattr(endpoint, "device_info", None)

    if not device_info:
        return {}

    return {
        "sn": device_info.get("serial_number"),
        "model": device_info.get("model"),
        "manufacturer": device_info.get("manufacturer"),
        "via_device": device_info.get("via_device"),
    }


def serialize_endpoint(endpoint: PowerOceanEndPoint) -> dict:
    return {
        "internal_unique_id": endpoint.internal_unique_id,
        "serial": endpoint.serial,
        "name": endpoint.name,
        "friendly_name": endpoint.friendly_name,
        "unit": endpoint.cls,
        "description": endpoint.description,
        "icon": endpoint.icon,
    }
