"""
devices.py: Devices of a PowerOcean system.

A system consists of one inverter (two in a dual installation) and the
devices connected to it: battery packs, wallbox, heating rod. Each device is
identified by its serial number; `via_sn` points to the device it is
connected through.

This module does not depend on Home Assistant.
"""

import base64
import binascii
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

LOGGER = logging.getLogger(__package__)


class PowerOceanModel(StrEnum):
    """Enumeration of supported PowerOcean device models with their internal codes."""

    POWEROCEAN = "83"
    POWEROCEAN_DC_FIT = "85"
    POWEROCEAN_SINGLE_PHASE = "86"
    POWEROCEAN_PLUS = "87"


MODEL_NAME_MAP = {
    PowerOceanModel.POWEROCEAN: "PowerOcean",
    PowerOceanModel.POWEROCEAN_DC_FIT: "PowerOcean DC fit",
    PowerOceanModel.POWEROCEAN_SINGLE_PHASE: "PowerOcean Single Phase",
    PowerOceanModel.POWEROCEAN_PLUS: "PowerOcean Plus",
}


@dataclass(frozen=True)
class Device:
    """A device of the system, such as inverter, battery pack or wallbox."""

    sn: str
    name: str
    model: str
    via_sn: str | None = None


def make_device(sn: str, prefix: str, model: str, via_sn: str | None = None) -> Device:
    """Return a device named `<prefix> <sn>`."""
    return Device(sn=sn, name=f"{prefix} {sn}", model=model, via_sn=via_sn or None)


def decode_sn(value: Any) -> str | None:
    """
    Return a serial number, decoded if it is Base64 encoded.

    Serial numbers that are no valid Base64 (or decode to no valid UTF-8)
    are returned unchanged.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8").strip()
    except binascii.Error:
        LOGGER.debug("Serial number is not Base64 encoded: %s", value)
        return value
    except UnicodeDecodeError:
        return value
