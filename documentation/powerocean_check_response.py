#!/usr/bin/env python3
"""
Check and compare parameter sets from EcoFlow PowerOcean API responses.

This script authenticates against the EcoFlow API, retrieves the latest
device data, and optionally compares it with a stored reference response.

Features:
    - Authenticate with the EcoFlow API and fetch current device data
    - Save the current API response to a file
    - Redact sensitive data (serial numbers, location, system name)
    - Compare current response with a reference JSON file
    - Detect new, removed, and changed keys/values
    - Generate human-readable (TXT/YAML) and machine-readable (JSON) diff reports

This tool is useful for monitoring structural or value changes in the
PowerOcean API or device configuration over time.

Usage:
    python -m documentation.powerocean_check_response [OPTIONS]

Examples:
    Save the current API response with redaction:
        python -m documentation.powerocean_check_response \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --save_new \
            --redact

    Compare current API response to a reference file:
        python -m documentation.powerocean_check_response \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2024-09-25_17-06-15.json

    Save differences in TXT and JSON format:
        python -m documentation.powerocean_check_response \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2024-09-25_17-06-15.json \
            --save_diff

    Generate a YAML diff report:
        python -m documentation.powerocean_check_response \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2025-08-15.json \
            --save_diff \
            --human_format yaml

For a full list of options:
    python -m documentation.powerocean_check_response --help

"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from custom_components.powerocean.api import EcoflowApi

SERIAL_NUMBER_LENGTH = 16


@dataclass
class DiffResult:
    """
    Container for structured differences between two API responses.

    Attributes:
        diff: Mapping of keys to their difference details.
              Each key contains values from the old and/or new response.
        new_keys: Keys present only in the new response.
        removed_keys: Keys present only in the old response.
        updated_keys: Keys present in both responses but with changed values.

    """

    diff: dict[str, Any]
    new_keys: list[str]
    removed_keys: list[str]
    updated_keys: list[str]


# Logging-Setup
logging.basicConfig(
    level=logging.INFO,  # Standard-Level
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# PowerOcean-Package zum Pfad hinzufügen
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent / "custom_components"


# =====================================
# Helper functions
# =====================================
def compare_lists(
    list1: list[Any],
    list2: list[Any],
    path: str,
    *,
    check_values: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Recursively compare two lists and return differences.

    Differences are returned in a dictionary where keys are in
    dotted-path notation with indices, e.g., 'parent[0].child'.

    Args:
        list1: First list to compare.
        list2: Second list to compare.
        path: Base path to prepend to diff keys.
        check_values: If True, value mismatches are included in the diff.
            If False, only structural differences (missing items) are reported.

    Returns:
        A dictionary of differences. Example:
        {
            "devices[0].status": {"in_dict1": "ok", "in_dict2": "error"},
            "devices[3]": {"in_dict2": {"id": 42}}
        }

    """
    diffs: dict[str, dict[str, Any]] = {}

    for i in range(max(len(list1), len(list2))):
        key_path = f"{path}[{i}]"
        if i < len(list1) and i < len(list2):
            a, b = list1[i], list2[i]
            if isinstance(a, dict) and isinstance(b, dict):
                sub_diffs = compare_dicts(
                    a, b, f"{key_path}.", check_values=check_values
                )
                diffs.update(sub_diffs)
            elif check_values and a != b:
                diffs[key_path] = {"in_dict1": a, "in_dict2": b}
        elif i < len(list1):
            diffs[key_path] = {"in_dict1": list1[i]}
        else:
            diffs[key_path] = {"in_dict2": list2[i]}

    return diffs


def group_keys_by_section(keys: list[str], depth: int = 2) -> dict[str, list[str]]:
    """
    Group dotted keys by their prefix up to `depth` segments.

    Example:
        depth=2 groups 'data.quota.JTS1.value' into 'data.quota'.

    Args:
        keys: List of dotted key strings, e.g., 'data.quota.JTS1.value'.
        depth: Number of segments to use for grouping (default 2).

    Returns:
        Dictionary mapping prefix to list of full keys.

    Example:
            {
                "data.quota": ["data.quota.JTS1.value", "data.quota.JTS2.value"],
                "data.status": ["data.status.online"]
            }

    """
    groups: dict[str, list[str]] = {}
    for k in keys:
        parts = k.split(".")
        prefix = ".".join(parts[:depth]) if len(parts) >= depth else parts[0]
        groups.setdefault(prefix, []).append(k)
    return groups


def format_value(
    value: Any,
    *,
    max_chars: int = 300,
    max_list_items: int = 6,
) -> str:
    """
    Return a human-friendly string representation of a value.

    - dict: pretty-printed JSON, truncated to ``max_chars``.
    - list: full JSON if short, otherwise preview of first ``max_list_items``.
    - str: truncated if longer than ``max_chars``.
    - primitives: converted via ``str``.
    - None: empty string.
    """

    def _truncate(text: str, suffix: str = "... (truncated)") -> str:
        return text if len(text) <= max_chars else f"{text[:max_chars]}{suffix}"

    result: str

    if value is None:
        result = ""

    elif isinstance(value, (int, float, bool)):
        result = str(value)

    elif isinstance(value, str):
        result = _truncate(value)

    else:
        try:
            if isinstance(value, dict):
                text = json.dumps(value, indent=2, ensure_ascii=False)
                result = _truncate(text, suffix="\n... (truncated)")

            elif isinstance(value, list):
                if len(value) > max_list_items:
                    preview = json.dumps(value[:max_list_items], ensure_ascii=False)
                    remainder = len(value) - max_list_items
                    result = f"{preview} ... (+{remainder} items)"
                else:
                    result = json.dumps(value, ensure_ascii=False)

            else:
                result = _truncate(str(value))

        except (TypeError, ValueError):
            # JSON serialization failed
            result = _truncate(str(value))

    return result


def compare_dicts(
    dict1: dict[str, Any],
    dict2: dict[str, Any],
    path: str = "",
    *,
    check_values: bool = True,
) -> dict[str, Any]:
    """Recursively compare two dictionaries and return their differences."""
    diffs: dict[str, Any] = {}

    keys1 = set(dict1)
    keys2 = set(dict2)

    # Keys only in dict1
    for key in keys1 - keys2:
        diffs[f"{path}{key}"] = {"in_dict1": dict1[key]}

    # Keys only in dict2
    for key in keys2 - keys1:
        diffs[f"{path}{key}"] = {"in_dict2": dict2[key]}

    # Keys in both
    for key in keys1 & keys2:
        value1 = dict1[key]
        value2 = dict2[key]
        current_path = f"{path}{key}"

        if isinstance(value1, dict) and isinstance(value2, dict):
            diffs.update(
                compare_dicts(
                    value1,
                    value2,
                    f"{current_path}.",
                    check_values=check_values,
                )
            )

        elif isinstance(value1, list) and isinstance(value2, list):
            diffs.update(
                compare_lists(
                    value1,
                    value2,
                    current_path,
                    check_values=check_values,
                )
            )

        elif check_values and value1 != value2:
            diffs[current_path] = {
                "in_dict1": value1,
                "in_dict2": value2,
            }

    return diffs


def count_keys_of_dict(data: Any) -> int:
    """Recursively count all dictionary keys in nested dict/list structures."""
    if isinstance(data, dict):
        return sum(1 + count_keys_of_dict(v) for v in data.values())

    if isinstance(data, list):
        return sum(count_keys_of_dict(item) for item in data)

    return 0


def apply_redact(data: Any) -> Any:
    """
    Recursively redact sensitive data from dictionaries/lists.

    1. Values for specific keys -> "REDACTED"
    2. Keys that are 16-char Serial Numbers -> "MY-SerialNumberX"
    """
    sn_map: dict[str, str] = {}
    sn_counter: int = 1

    def _redact_recursive(obj: Any) -> Any:
        nonlocal sn_counter

        if isinstance(obj, dict):
            new_dict: dict[Any, Any] = {}

            for k, v in obj.items():
                # Redact values for specific keys
                if k in {
                    "systemName",
                    "createTime",
                    "location",
                    "timezone",
                    "moduleSn",
                    "bpSn",
                    "wireless4gIccid",
                    "evSn",
                    "devSn",
                    "eagleEyeTraceId",
                    "tid",
                } and isinstance(v, str):
                    new_dict[k] = "REDACTED"
                    continue

                # Replace serial-number-like keys
                new_key = k
                if (
                    isinstance(k, str)
                    and len(k) == SERIAL_NUMBER_LENGTH
                    and k.isalnum()
                    and k.isupper()
                ):
                    if k not in sn_map:
                        sn_map[k] = f"MY-SerialNumber{sn_counter}"
                        sn_counter += 1
                    new_key = sn_map[k]

                new_dict[new_key] = _redact_recursive(v)

            return new_dict

        if isinstance(obj, list):
            return [_redact_recursive(item) for item in obj]

        if isinstance(obj, str):
            stripped = obj.strip()

            # Try parsing embedded JSON
            if (stripped.startswith("{") and stripped.endswith("}")) or (
                stripped.startswith("[") and stripped.endswith("]")
            ):
                try:
                    inner_data = json.loads(obj)
                    redacted_inner = _redact_recursive(inner_data)
                    return json.dumps(redacted_inner, indent=2)
                except Exception:
                    logger.exception("Failed to parse embedded JSON for redaction.")

            # Replace known serial numbers inside strings
            for original_sn, placeholder in sn_map.items():
                if original_sn in obj:
                    obj = obj.replace(original_sn, placeholder)

            return obj

        return obj

    return _redact_recursive(data)


def build_parser() -> argparse.ArgumentParser:
    """
    Create and configure the command-line argument parser.

    Returns:
        argparse.ArgumentParser: Configured parser for the PowerOcean
        parameter check CLI including authentication, diff handling,
        output formatting and redaction options.

    """
    parser = argparse.ArgumentParser(description="Check PowerOcean parameters.")

    parser.add_argument(
        "--sn",
        default="MY_SERIAL_NUMBER",
        help="Serial number (default: MY_SERIAL_NUMBER)",
    )

    parser.add_argument(
        "--username",
        default="MY_USERNAME",
        help="Username (default: MY_USERNAME)",
    )

    parser.add_argument(
        "--password",
        default="MY_PASSWORD",
        help="Password (default: MY_PASSWORD)",
    )

    parser.add_argument(
        "--variant",
        default="MY_VARIANT",
        help="Variant (e.g. 83, 85, 86, 87)",
    )

    parser.add_argument(
        "--fn_json",
        help="Reference JSON file for comparison",
    )

    parser.add_argument(
        "--save_new",
        action="store_true",
        help="Save current response to data directory",
    )

    parser.add_argument(
        "--save_diff",
        action="store_true",
        help="Save differences to data directory",
    )

    parser.add_argument(
        "--diff_mode",
        choices=["txt", "json", "both"],
        default="both",
        help="Which diff files to save (default: both)",
    )

    parser.add_argument(
        "--human_format",
        choices=["txt", "yaml"],
        default="txt",
        help="Format of human-readable report (default: txt)",
    )

    parser.add_argument(
        "--redact",
        action="store_true",
        help="Redact sensitive data in saved response",
    )

    return parser


async def fetch_current_response(args: Any) -> dict[str, Any]:
    """
    Authorize against the EcoFlow API and fetch the current raw response.

    Args:
        args: Parsed CLI arguments containing:
            - sn: Serial number
            - username: Account username
            - password: Account password
            - variant: Device variant

    Returns:
        dict[str, Any]: Raw JSON response from the API.

    Raises:
        Exception: Propagates authorization or network errors.

    """
    ef = EcoflowApi(args.sn, args.username, args.password, args.variant)

    logger.info("Authorizing for %s...", args.username)
    await ef.async_authorize()

    logger.info("Fetching current data...")
    try:
        response = await ef.fetch_raw()
    finally:
        await ef.close()

    return response


def calculate_diff(
    old: dict[str, Any],
    new: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    """
    Compare two dictionaries and classify their differences.

    Args:
        old: Reference dictionary.
        new: Current dictionary.

    Returns:
        A tuple containing:
            - diff: Full diff structure as returned by compare_dicts
            - new_keys: Keys only present in the new dictionary
            - removed_keys: Keys only present in the old dictionary
            - updated_keys: Keys present in both dictionaries but with changed values

    """
    diff = compare_dicts(old, new, check_values=True)

    new_keys = [k for k, v in diff.items() if "in_dict2" in v and "in_dict1" not in v]

    removed_keys = [
        k for k, v in diff.items() if "in_dict1" in v and "in_dict2" not in v
    ]

    updated_keys = [k for k, v in diff.items() if "in_dict1" in v and "in_dict2" in v]

    return diff, new_keys, removed_keys, updated_keys


def resolve_reference_file(fn: str) -> str | None:
    """
    Resolve the reference JSON file path.

    Checks:
    1. Provided path
    2. BASE_DIR

    Returns:
        Full path as string if file exists, otherwise None.

    """
    path = Path(fn)

    if path.exists():
        return str(path)

    fn_in_base = BASE_DIR / fn
    if fn_in_base.exists():
        return str(fn_in_base)

    return None


def print_summary(
    new_keys: list[str], removed_keys: list[str], updated_keys: list[str]
) -> None:
    """Print a concise summary of new, removed, and updated keys."""
    logger.info("Comparison Summary:")
    logger.info("- Number of new keys: %d", len(new_keys))
    logger.info("- Number of removed keys: %d", len(removed_keys))
    logger.info("- Number of updated keys: %d\n", len(updated_keys))


@dataclass
class DiffArgs:
    """Configuration options for diff report generation."""

    human_format: Literal["txt", "yaml"]
    diff_mode: Literal["txt", "json", "both"]


def save_diff_reports(
    result: DiffResult,
    date_str: str,
    args: DiffArgs,
) -> None:
    """
    Save API response differences to disk.

    Creates:
        - Human-readable report (YAML or TXT)
        - Optional machine-readable JSON diff

    Args:
        result: DiffResult containing diff data and key changes.
        date_str: Timestamp string for filenames.
        args: Configuration controlling output format and diff mode.

    """
    human_ok = True

    # -------------------------
    # YAML report
    # -------------------------
    if args.human_format == "yaml":
        try:
            fn_human = BASE_DIR / f"Response-Difference_{date_str}.yaml"

            report = {
                "old_version": "Reference JSON",
                "new_version": f"Current API response ({date_str})",
                "counts": {
                    "new": len(result.new_keys),
                    "removed": len(result.removed_keys),
                    "updated": len(result.updated_keys),
                },
                "new": {k: result.diff[k].get("in_dict2") for k in result.new_keys},
                "removed": {
                    k: result.diff[k].get("in_dict1") for k in result.removed_keys
                },
                "updated": {
                    k: {
                        "before": result.diff[k].get("in_dict1"),
                        "after": result.diff[k].get("in_dict2"),
                    }
                    for k in result.updated_keys
                },
            }

            with fn_human.open("w", encoding="utf-8") as f:
                yaml.safe_dump(report, f, sort_keys=False, allow_unicode=True)

            logger.info("Saved YAML differences to %s", fn_human)

        except (ImportError, AttributeError, OSError) as err:
            human_ok = False
            logger.warning("Failed to create YAML report: %s", err)

    # -------------------------
    # TXT fallback
    # -------------------------
    if args.human_format == "txt" or not human_ok:
        fn_human = BASE_DIR / f"Response-Difference_{date_str}.txt"

        with fn_human.open("w", encoding="utf-8") as f:
            f.write("Comparison Report\n=================\n\n")
            f.write("Old version: Reference JSON\n")
            f.write(f"New version: Current API response ({date_str})\n\n")
            f.write(f"Number of new keys: {len(result.new_keys)}\n")
            f.write(f"Number of removed keys: {len(result.removed_keys)}\n")
            f.write(f"Number of updated keys: {len(result.updated_keys)}\n\n")

        logger.info("Saved TXT differences to %s", fn_human)

    # -------------------------
    # JSON diff
    # -------------------------
    if args.diff_mode in ("json", "both"):
        fn_json = BASE_DIR / f"Response-Difference_{date_str}.json"

        try:
            with fn_json.open("w", encoding="utf-8") as fjson:
                json.dump(result.diff, fjson, indent=2, ensure_ascii=False)

            logger.info("Saved JSON diff to %s", fn_json)

        except OSError:
            logger.exception("Failed to save JSON diff.")


async def run_check() -> None:
    """
    Execute the PowerOcean parameter check workflow.

    - Fetch current API response
    - Optionally save it
    - Optionally compare with reference JSON
    - Optionally write diff reports
    """
    parser = build_parser()
    parsed_args = parser.parse_args()

    # Convert argparse.Namespace -> DiffArgs (typed)
    diff_args = DiffArgs(
        human_format=parsed_args.human_format,
        diff_mode=parsed_args.diff_mode,
    )

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    try:
        response = await fetch_current_response(parsed_args)
    except Exception:
        logger.exception("Auth/API failed.")
        return

    nkeys_new = count_keys_of_dict(response)
    logger.info("Current response has %d keys.", nkeys_new)

    # -------------------------
    # Save new response
    # -------------------------
    if parsed_args.save_new:
        if parsed_args.redact:
            response = apply_redact(response)

        fnout = BASE_DIR / f"Response-EcoFlowAPI_{date_str}.json"

        await asyncio.to_thread(
            _write_json_file,
            fnout,
            response,
        )

        logger.info("Saved current response to %s", fnout)

    if not parsed_args.fn_json:
        return

    # -------------------------
    # Load reference file
    # -------------------------
    fn_ref = resolve_reference_file(parsed_args.fn_json)
    if not fn_ref:
        logger.warning("Reference file not found.")
        return

    response_old = await asyncio.to_thread(_read_json_file, Path(fn_ref))

    diff, new_keys, removed_keys, updated_keys = calculate_diff(
        response_old,
        response,
    )

    print_summary(new_keys, removed_keys, updated_keys)

    # -------------------------
    # Save diff reports
    # -------------------------
    if parsed_args.save_diff:
        save_diff_reports(
            result=DiffResult(
                diff=diff,
                new_keys=new_keys,
                removed_keys=removed_keys,
                updated_keys=updated_keys,
            ),
            date_str=date_str,
            args=diff_args,
        )


# -------------------------
# Blocking helpers
# -------------------------


def _write_json_file(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _read_json_file(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


async def main() -> None:
    """Program entry point."""
    await run_check()


if __name__ == "__main__":
    asyncio.run(main())
