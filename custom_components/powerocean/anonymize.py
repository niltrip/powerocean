"""
anonymize.py: Pseudonymize EcoFlow API responses for diagnostics and fixtures.

Serial numbers are replaced consistently by placeholders such as `SN_BP01`
instead of being blanked out, so the device structure stays readable: the
same serial number gets the same placeholder wherever it appears - as plain
value, Base64 encoded, as dict key or inside an embedded JSON string. The
encoding is kept, so an anonymized response parses like the original one and
can be used as test fixture.

Other personal data (system name, location, SIM card id, ...) is redacted.

This module does not depend on Home Assistant.
"""

import base64
import binascii
import json
import re
from typing import Any

REDACTED = "**REDACTED**"

# Keys holding a serial number: bpSn, devSn, evSn, hrSn, moduleSn, ...
SN_KEY = re.compile(r"sn$", re.IGNORECASE)

# Keys of reports whose dict keys are serial numbers
SN_DICT_KEYS = {"parallel": "INV"}
SN_DICT_SUFFIXES = {"_BP_STA_REPORT": "BP"}
NON_SN_DICT_KEYS = {"", "updateTime"}

# Placeholder hints, most specific first; others come from the key (moduleSn)
HINT_PRIORITY = {hint: i for i, hint in enumerate(("INV", "BP", "EV", "HR", "DEV"))}

# Personal data without structural meaning
REDACT_KEYS = {
    "chargeVehicleId",
    "createTime",
    "defaultVehicleId",
    "eagleEyeTraceId",
    "location",
    "systemName",
    "wireless4gIccid",
}

# Shorter values are placeholders like "xxxx" in user provided responses
MIN_SN_LENGTH = 6
SN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{MIN_SN_LENGTH},}}")


def _decode_b64_sn(value: str) -> str | None:
    """Return the serial number a Base64 string encodes, None if it is none."""
    try:
        decoded = base64.b64decode(value, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return decoded if SN_PATTERN.fullmatch(decoded) else None


def _load_embedded(value: str) -> dict | list | None:
    """Return the JSON object a string contains (e.g. battery payloads)."""
    if not value.lstrip().startswith(("{", "[")):
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return None
    return data if isinstance(data, dict | list) else None


def _is_device_payload(value: Any) -> bool:
    """Return True for a device payload, given as dict or as JSON string."""
    return isinstance(value, dict) or (
        isinstance(value, str) and isinstance(_load_embedded(value), dict)
    )


def _sn_dict_hint(key: str | None) -> str | None:
    """Return the placeholder hint if the dict under `key` is keyed by SNs."""
    if key is None:
        return None
    if key in SN_DICT_KEYS:
        return SN_DICT_KEYS[key]
    return next(
        (hint for suffix, hint in SN_DICT_SUFFIXES.items() if key.endswith(suffix)),
        None,
    )


class Anonymizer:
    """
    Replace serial numbers consistently and redact personal data.

    One instance keeps its placeholders, so several objects anonymized with
    the same instance (e.g. API response and device list) match each other.
    """

    def __init__(self, main_sn: str | None = None) -> None:
        """Initialize, optionally with the serial number of the main device."""
        self._aliases: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._pattern: re.Pattern[str] | None = None
        if main_sn:
            self._aliases[main_sn] = "SN_MAIN"

    @property
    def aliases(self) -> dict[str, str]:
        """Return the mapping of serial numbers to placeholders."""
        return dict(self._aliases)

    def anonymize(self, data: Any) -> Any:
        """Return an anonymized copy of `data`."""
        candidates: dict[str, str] = {}
        self._collect(data, None, candidates)
        for sn, hint in candidates.items():
            if sn not in self._aliases:
                self._counters[hint] = self._counters.get(hint, 0) + 1
                self._aliases[sn] = f"SN_{hint}{self._counters[hint]:02d}"
                self._pattern = None
        return self._replace(data)

    # Pass 1: find serial numbers, each with the most specific hint found

    @staticmethod
    def _add_candidate(candidates: dict[str, str], value: str, hint: str) -> None:
        sn = _decode_b64_sn(value) or value
        if not SN_PATTERN.fullmatch(sn):
            return
        current = candidates.get(sn)
        if current is None or HINT_PRIORITY.get(hint, len(HINT_PRIORITY)) < (
            HINT_PRIORITY.get(current, len(HINT_PRIORITY))
        ):
            candidates[sn] = hint

    def _collect(self, data: Any, key: str | None, candidates: dict[str, str]) -> None:
        if isinstance(data, dict):
            hint = _sn_dict_hint(key)
            for k, v in data.items():
                if (
                    hint
                    and isinstance(k, str)
                    and k not in NON_SN_DICT_KEYS
                    and _is_device_payload(v)
                ):
                    self._add_candidate(candidates, k, hint)
                self._collect(v, k, candidates)
        elif isinstance(data, list):
            for item in data:
                self._collect(item, key, candidates)
        elif isinstance(data, str):
            embedded = _load_embedded(data)
            if embedded is not None:
                self._collect(embedded, key, candidates)
            elif key and SN_KEY.search(key):
                self._add_candidate(candidates, data, key[:-2].upper() or "DEV")

    # Pass 2: replace

    def _replace(self, data: Any, key: str | None = None) -> Any:
        if isinstance(data, dict):
            return {
                self._substitute(k) if isinstance(k, str) else k: (
                    REDACTED
                    if k in REDACT_KEYS and v not in ("", None)
                    else self._replace(v, k)
                )
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._replace(item, key) for item in data]
        if isinstance(data, str):
            embedded = _load_embedded(data)
            if embedded is not None:
                return json.dumps(self._replace(embedded, key), indent=2)
            return self._substitute(data)
        return data

    def _substitute(self, value: str) -> str:
        """Replace serial numbers in a string, keeping a Base64 encoding."""
        if not self._aliases:
            return value
        decoded = _decode_b64_sn(value)
        if decoded is not None and decoded in self._aliases:
            return base64.b64encode(self._aliases[decoded].encode()).decode()
        if self._pattern is None:
            # One pass, longest first, so overlapping serial numbers can't mix
            self._pattern = re.compile(
                "|".join(
                    re.escape(sn) for sn in sorted(self._aliases, key=len, reverse=True)
                )
            )
        return self._pattern.sub(lambda m: self._aliases[m.group()], value)
