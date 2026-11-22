"""
diagnostics.py: Diagnostics for the PowerOcean integration.

The download contains the last raw API response and the devices the parser
found, with their current values. Serial numbers are replaced consistently
by placeholders (see `anonymize.py`), so the device structure stays readable
and the response can be used as test fixture.

With the MQTT option and debug logging on, it also contains the MQTT
messages of the last minutes, masked (see `mqtt_capture.py`): to find out
how another model reports and which commands the app sends (issue #198).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .anonymize import Anonymizer, redacted_values
from .const import LOGGER
from .coordinator import PowerOceanConfigEntry, PowerOceanCoordinator
from .mqtt import PowerOceanMqtt
from .mqtt_capture import Masker, summary

# The serial number is part of the entry's unique_id and title
TO_REDACT = {CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
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
            "mqtt": coordinator.mqtt is not None,
        },
        # Firmware of the modules from MQTT, by address (without serials)
        "modules": {
            address: {
                "type": module.module_type,
                "firmware": module.firmware,
                "loader": module.loader,
            }
            for address, module in sorted(coordinator.modules.items())
        },
        **anonymized,
        "mqtt_messages": await hass.async_add_executor_job(
            _mqtt_messages,
            coordinator.mqtt,
            [
                entry.data.get(CONF_EMAIL, ""),
                entry.data.get(CONF_PASSWORD, ""),
                # Also short ones, e.g. the vehicle id of a wallbox (#199)
                *redacted_values(coordinator.last_response),
            ],
        ),
    }


def _mqtt_messages(mqtt: PowerOceanMqtt | None, private: list[str]) -> dict[str, Any]:
    """
    Return the recorded MQTT messages, masked.

    Recorded only with the MQTT option and while debug logging is on. A
    message that still holds a secret after masking is left out.
    """
    result: dict[str, Any] = {
        "debug_logging": LOGGER.isEnabledFor(logging.DEBUG),
        "messages": [],
    }
    if mqtt is None:
        return result
    masker = Masker(mqtt.serial, mqtt.user_id, [*mqtt.private, *private])
    messages = mqtt.recorder.messages()
    # First learn every serial and text, so the check of each message below
    # knows also those that only a later message shows
    for message in messages:
        masker.payload(message.payload)
    for message in messages:
        masked = masker.payload(message.payload)
        topic = masker.topic(message.topic)
        record: dict[str, Any] = {
            "time_utc": datetime.fromtimestamp(message.time, UTC).isoformat(
                timespec="milliseconds"
            ),
            "topic": topic,
        }
        if masker.leaks_in(masked) or masker.leaks(topic):
            record["left_out"] = "masking incomplete"
        else:
            record["cmds"] = summary(masked)
            record["hex"] = masked.hex()
        result["messages"].append(record)
    return result


def _devices(coordinator: PowerOceanCoordinator) -> dict[str, Any]:
    """Group the endpoints by device, with their current values."""
    values = coordinator.data or {}
    devices: dict[str, Any] = {}
    for unique_id, endpoint in coordinator.endpoints.items():
        info = endpoint.device_info or {}
        device = devices.setdefault(
            endpoint.serial,
            {
                "name": info.get("name"),
                "model": info.get("model"),
                "model_id": info.get("model_id"),
                "sw_version": coordinator.firmware.get(endpoint.serial),
                "via_device": endpoint.via_serial,
                "datapoints": {},
            },
        )
        # unique_id = <serial>_<report>_<key>
        datapoint = unique_id.removeprefix(f"{endpoint.serial}_")
        device["datapoints"][datapoint] = values.get(unique_id)
    return devices
