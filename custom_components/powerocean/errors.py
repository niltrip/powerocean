"""
errors.py: Active errors of a PowerOcean system.

The `*_ERROR_CHANGE_REPORT` lists the active error codes per module: EMS,
PCS and each battery pack. Cause and solution of a code are only shown in
the EcoFlow web portal: its API requires a signature the integration cannot
create. Name and level of known codes come from `KNOWN_ERRORS`.

This module does not depend on Home Assistant.
"""

import base64
import binascii
from dataclasses import asdict, dataclass
from typing import Any

# Report keys with the error codes of one module (dict) or several (list)
MODULE_KEYS = {"emsErrCode": "ems", "pcsErrCode": "pcs", "bpErrCode": "bp"}

# Device a module belongs to, as the portal lists it (EMS and PCS are both
# "Hybrid Inverter"). The same code can mean something else on another device.
MODULE_DEVICES = {"ems": "inverter", "pcs": "inverter", "bp": "battery"}

# Name and level (Info, Warning, Error) of codes seen in the EcoFlow web
# portal's error history, by device and code. Add new codes from issues.
KNOWN_ERRORS: dict[tuple[str, int], tuple[str, str]] = {
    ("inverter", 75): ("PV1 bus slow undervoltage", "Error"),
    ("inverter", 145): ("Grid relay overcurrent alarm", "Warning"),
    ("inverter", 149): (
        "BACKUP port overload warning (grid-connected operation)",
        "Info",
    ),
    ("inverter", 521): ("Energy storage communication error", "Warning"),
    ("inverter", 702): ("Low power mode", "Info"),
    ("battery", 203): ("BP module power-on notification", "Info"),
}


@dataclass(frozen=True)
class ActiveError:
    """An active error of one module, with name and level if the code is known."""

    module: str
    module_sn: str | None
    code: int
    name: str | None = None
    level: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the error as dict, without name and level if unknown."""
        return {key: value for key, value in asdict(self).items() if value is not None}


def parse_error_report(report: dict[str, Any]) -> list[ActiveError]:
    """Return the active errors listed in an `*_ERROR_CHANGE_REPORT`."""
    errors: list[ActiveError] = []
    for key, module in MODULE_KEYS.items():
        entries = report.get(key)
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("errCode"), list
            ):
                continue
            module_sn = _decode_sn(entry.get("moduleSn"))
            errors.extend(
                _active_error(module, module_sn, code)
                for raw_code in entry["errCode"]
                if (code := _to_code(raw_code)) is not None
            )
    return errors


def _active_error(module: str, module_sn: str | None, code: int) -> ActiveError:
    """Return an active error, with name and level if the code is known."""
    name, level = KNOWN_ERRORS.get((MODULE_DEVICES[module], code), (None, None))
    return ActiveError(
        module=module, module_sn=module_sn, code=code, name=name, level=level
    )


def _to_code(value: Any) -> int | None:
    """Return an error code given as int or numeric string, None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _decode_sn(value: Any) -> str | None:
    """Return a module serial number, decoded if it is Base64 encoded."""
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = base64.b64decode(value, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value
    return decoded if decoded.isprintable() and decoded.isascii() else value
