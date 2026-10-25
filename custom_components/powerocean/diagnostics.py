"""
diagnostics.py: Diagnostics for the PowerOcean integration.

The download contains the last raw API response and the devices the parser
found, with their current values. Serial numbers are replaced consistently
by placeholders (see `anonymize.py`), so the device structure stays readable
and the response can be used as test fixture.
"""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .anonymize import Anonymizer
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator

# The serial number is part of the entry's unique_id and title
TO_REDACT = {CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: PowerOceanConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    anonymized = Anonymizer(entry.data.get(CONF_DEVICE_ID)).anonymize(
        {
            "api_response": coordinator.last_response,
            "devices": _devices(coordinator),
        }
    )

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "api_host": coordinator.api.api_host,
            "variant": coordinator.api.variant,
        },
        **anonymized,
    }


def _devices(coordinator: PowerOceanCoordinator) -> dict[str, Any]:
    """Group the endpoints by device, with their current values."""
    values = coordinator.data or {}
    devices: dict[str, Any] = {}
    for unique_id, endpoint in coordinator.endpoints.items():
        info = endpoint.device_info or {}
        via_device = info.get("via_device")
        device = devices.setdefault(
            endpoint.serial,
            {
                "name": info.get("name"),
                "model": info.get("model"),
                "model_id": info.get("model_id"),
                "sw_version": coordinator.firmware.get(endpoint.serial),
                "via_device": via_device[1] if via_device else None,
                "datapoints": {},
            },
        )
        # unique_id = <serial>_<report>_<key>
        datapoint = unique_id.removeprefix(f"{endpoint.serial}_")
        device["datapoints"][datapoint] = values.get(unique_id)
    return devices
