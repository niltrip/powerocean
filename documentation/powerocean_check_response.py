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
    python3 powerocean_check_response.py [OPTIONS]

Examples:
    Save the current API response with redaction:
        python3 powerocean_check_response.py \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --save_new \
            --redact

    Compare current API response to a reference file:
        python3 powerocean_check_response.py \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2024-09-25_17-06-15.json

    Save differences in TXT and JSON format:
        python3 powerocean_check_response.py \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2024-09-25_17-06-15.json \
            --save_diff

    Generate a YAML diff report:
        python3 powerocean_check_response.py \
            --username your@email.com \
            --password yourpassword \
            --sn your_sn \
            --variant 83 \
            --fn_json powerocean/Response-EcoFlowAPI_2025-08-15.json \
            --save_diff \
            --human_format yaml

For a full list of options:
    python3 powerocean_check_response.py --help

"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

import yaml

# PowerOcean-Package zum Pfad hinzufügen
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(BASE_DIR, "../custom_components")
sys.path.append(PROJECT_ROOT)

from powerocean.api import EcoflowApi


# =====================================
# Helper functions
# =====================================
def compare_lists(list1, list2, path, check_values):
    diffs = {}
    for i in range(max(len(list1), len(list2))):
        if i < len(list1) and i < len(list2):
            if isinstance(list1[i], dict) and isinstance(list2[i], dict):
                sub_diffs = compare_dicts(
                    list1[i], list2[i], f"{path}[{i}].", check_values
                )
                diffs.update(sub_diffs)
            elif check_values and list1[i] != list2[i]:
                diffs[f"{path}[{i}]"] = {"in_dict1": list1[i], "in_dict2": list2[i]}
        elif i < len(list1):
            diffs[f"{path}[{i}]"] = {"in_dict1": list1[i]}
        else:
            diffs[f"{path}[{i}]"] = {"in_dict2": list2[i]}
    return diffs


def group_keys_by_section(keys, depth=2):
    """
    Group dotted keys by their prefix up to `depth` segments.

    Example: depth=2 groups 'data.quota.JTS1...' into 'data.quota'.
    """
    groups = {}
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
        if len(text) > max_chars:
            return f"{text[:max_chars]}{suffix}"
        return text

    if value is None:
        return ""

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, str):
        return _truncate(value)

    try:
        if isinstance(value, dict):
            return _truncate(
                json.dumps(value, indent=2, ensure_ascii=False),
                suffix="\n... (truncated)",
            )

        if isinstance(value, list):
            if len(value) > max_list_items:
                preview = json.dumps(value[:max_list_items], ensure_ascii=False)
                return f"{preview} ... (+{len(value) - max_list_items} items)"
            return json.dumps(value, ensure_ascii=False)

        return _truncate(str(value))

    except (TypeError, ValueError):
        # JSON serialization failed
        try:
            return _truncate(str(value))
        except Exception:
            return "<unprintable>"


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


def apply_redact(data):
    """
    Recursively redact sensitive data from the dictionary/list.

    1. Values for keys: systemName, createTime, location, timezone -> "REDACTED"
    2. Keys that are 16-char Serial Numbers -> "MY-SerialNumberX".
    """
    sn_map = {}
    sn_counter = 1

    def _redact_recursive(obj):
        nonlocal sn_counter
        if isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                # Redact values for specific keys
                if k in [
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
                ] and isinstance(v, str):
                    new_dict[k] = "REDACTED"
                    continue

                # Check for SN keys (16 chars, alphanumeric)
                new_key = k
                if len(k) == 16 and k.isalnum() and k.isupper():
                    if k not in sn_map:
                        sn_map[k] = f"MY-SerialNumber{sn_counter}"
                        sn_counter += 1
                    new_key = sn_map[k]
                new_dict[new_key] = _redact_recursive(v)
            return new_dict
        elif isinstance(obj, list):
            return [_redact_recursive(item) for item in obj]
        elif isinstance(obj, str):
            # Check if it's a JSON string
            if (obj.strip().startswith("{") and obj.strip().endswith("}")) or (
                obj.strip().startswith("[") and obj.strip().endswith("]")
            ):
                try:
                    inner_data = json.loads(obj)
                    redacted_inner = _redact_recursive(inner_data)
                    return json.dumps(redacted_inner, indent=2)
                except Exception:
                    pass

            # Key-value redaction for strings that are not JSON but contain SNs
            for original_sn, placeholder in sn_map.items():
                if original_sn in obj:
                    obj = obj.replace(original_sn, placeholder)
            return obj
        else:
            return obj

    return _redact_recursive(data)


async def run_check():
    parser = argparse.ArgumentParser(description="Check PowerOcean parameters.")
    parser.add_argument("--sn", default="MY_SERIAL_NUMBER", help="Serial number")
    parser.add_argument("--username", default="MY_USERNAME", help="Username")
    parser.add_argument("--password", default="MY_PASSWORD", help="Password")
    parser.add_argument(
        "--variant", default="MY_VARIANT", help="Variant (e.g. 83, 85, 86, 87)"
    )
    parser.add_argument(
        "--fn_json",
        help="Reference JSON file for comparison (searched in data/powerocean by default)",
    )
    parser.add_argument(
        "--save_new",
        action="store_true",
        help="Save current response to data/powerocean",
    )
    parser.add_argument(
        "--save_diff", action="store_true", help="Save differences to data/powerocean"
    )
    parser.add_argument(
        "--diff_mode",
        choices=["txt", "json", "both"],
        default="both",
        help="Which diff files to save: txt (human), json (machine), or both",
    )
    parser.add_argument(
        "--human_format",
        choices=["txt", "yaml"],
        default="txt",
        help="Format of human-readable report when saving (txt or yaml)",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help="Redact sensitive data (SNs, location, etc.) in saved new response",
    )

    args = parser.parse_args()

    # Ensure data directory exists
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")

    # Init and authorize
    ef = EcoflowApi(args.sn, args.username, args.password, args.variant)
    print(f"Authorizing for {args.username}...")
    try:
        await ef.async_authorize()
    except Exception as e:
        print(f"Auth failed: {e}")
        return

    print("Fetching current data...")

    response = await ef.fetch_raw()
    await ef.close()
    nkeys_new = count_keys_of_dict(response)
    print(f"Current response has {nkeys_new} keys.")

    if args.save_new:
        if args.redact:
            print("Redacting sensitive data...")
            response = apply_redact(response)

        fnout = os.path.join(BASE_DIR, f"Response-EcoFlowAPI_{date_str}.json")
        with open(fnout, "w") as f:
            json.dump(response, f, indent=2)
        print(f"Saved current response to {fnout}")

    fn_ref = args.fn_json
    if fn_ref:
        # If file not found in current dir, check data dir
        if not os.path.exists(fn_ref):
            fn_ref_in_data = os.path.join(BASE_DIR, fn_ref)
            if os.path.exists(fn_ref_in_data):
                fn_ref = fn_ref_in_data

        if os.path.exists(fn_ref):
            print(f"Comparing with {fn_ref}...")
            with open(fn_ref) as f:
                response_old = json.load(f)

            nkeys_old = count_keys_of_dict(response_old)
            diff = compare_dicts(response_old, response, check_values=True)

            if not diff:
                print(">>> No new parameters encountered!")
            else:
                # Categorize differences
                new_keys = [
                    k
                    for k, v in diff.items()
                    if "in_dict2" in v and "in_dict1" not in v
                ]
                removed_keys = [
                    k
                    for k, v in diff.items()
                    if "in_dict1" in v and "in_dict2" not in v
                ]
                updated_keys = [
                    k for k, v in diff.items() if "in_dict1" in v and "in_dict2" in v
                ]

                print("Differences found:")
                print(f"- Number of new keys: {len(new_keys)}")
                print(f"- Number of removed keys: {len(removed_keys)}")
                print(f"- Number of updated keys: {len(updated_keys)}")
                print("")

                # Group by top-level sections for readability
                new_groups = group_keys_by_section(new_keys, depth=2)
                removed_groups = group_keys_by_section(removed_keys, depth=2)
                updated_groups = group_keys_by_section(updated_keys, depth=2)

                if new_keys:
                    print("New keys (grouped):")
                    for sec, keys_in_sec in sorted(new_groups.items()):
                        print(f"- {sec}:")
                        for k in keys_in_sec:
                            print(
                                f"    - {k.split('.')[-1]}: {format_value(diff[k].get('in_dict2'))}"
                            )
                    print("")

                if removed_keys:
                    print("Removed keys (grouped):")
                    for sec, keys_in_sec in sorted(removed_groups.items()):
                        print(f"- {sec}:")
                        for k in keys_in_sec:
                            print(
                                f"    - {k.split('.')[-1]}: {format_value(diff[k].get('in_dict1'))}"
                            )
                    print("")

                if updated_keys:
                    print("Updated keys (grouped):")
                    for sec, keys_in_sec in sorted(updated_groups.items()):
                        print(f"- {sec}:")
                        for k in keys_in_sec:
                            before = format_value(diff[k].get("in_dict1"))
                            after = format_value(diff[k].get("in_dict2"))
                            print(f"    - {k.split('.')[-1]}: {before} -> {after}")
                    print("")

                if args.save_diff:
                    # Human-readable report (TXT or YAML)
                    human_ok = True
                    if args.human_format == "yaml":
                        try:
                            fn_human = os.path.join(
                                BASE_DIR, f"Response-Difference_{date_str}.yaml"
                            )
                            with open(fn_human, "w") as f:
                                # Build structured report dict for YAML
                                report = {
                                    "old_version": fn_ref,
                                    "new_version": f"Current API response ({date_str})",
                                    "total_keys_old": nkeys_old,
                                    "total_keys_new": nkeys_new,
                                    "counts": {
                                        "new": len(new_keys),
                                        "removed": len(removed_keys),
                                        "updated": len(updated_keys),
                                    },
                                    "new": {
                                        sec: [
                                            {"key": k, "value": diff[k].get("in_dict2")}
                                            for k in keys_in_sec
                                        ]
                                        for sec, keys_in_sec in sorted(
                                            new_groups.items()
                                        )
                                    },
                                    "removed": {
                                        sec: [
                                            {"key": k, "value": diff[k].get("in_dict1")}
                                            for k in keys_in_sec
                                        ]
                                        for sec, keys_in_sec in sorted(
                                            removed_groups.items()
                                        )
                                    },
                                    "updated": {
                                        sec: [
                                            {
                                                "key": k,
                                                "before": diff[k].get("in_dict1"),
                                                "after": diff[k].get("in_dict2"),
                                            }
                                            for k in keys_in_sec
                                        ]
                                        for sec, keys_in_sec in sorted(
                                            updated_groups.items()
                                        )
                                    },
                                }
                                yaml.safe_dump(
                                    report, f, sort_keys=False, allow_unicode=True
                                )
                            print(f"Saved YAML differences to {fn_human}")
                        except Exception:
                            human_ok = False
                            print(
                                "Warning: PyYAML not available; falling back to TXT report."
                            )

                    if args.human_format == "txt" or not human_ok:
                        fn_human = os.path.join(
                            BASE_DIR, f"Response-Difference_{date_str}.txt"
                        )
                        with open(fn_human, "w") as f:
                            f.write("Comparison Report\n")
                            f.write("=================\n\n")
                            f.write(f"Old version: {fn_ref}\n")
                            f.write(
                                f"New version: Current API response ({date_str})\n\n"
                            )
                            f.write(f"Total keys in old version: {nkeys_old}\n")
                            f.write(f"Total keys in new version: {nkeys_new}\n\n")
                            f.write(f"Number of new keys: {len(new_keys)}\n")
                            f.write(f"Number of removed keys: {len(removed_keys)}\n")
                            f.write(f"Number of updated keys: {len(updated_keys)}\n\n")

                            if new_keys:
                                f.write("New Keys (grouped):\n")
                                for sec, keys_in_sec in sorted(new_groups.items()):
                                    f.write(f"- {sec}:\n")
                                    for k in keys_in_sec:
                                        f.write(
                                            f"    - {k.split('.')[-1]}: {format_value(diff[k].get('in_dict2'))}\n"
                                        )
                                f.write("\n")

                            if removed_keys:
                                f.write("Removed Keys (grouped):\n")
                                for sec, keys_in_sec in sorted(removed_groups.items()):
                                    f.write(f"- {sec}:\n")
                                    for k in keys_in_sec:
                                        f.write(
                                            f"    - {k.split('.')[-1]}: {format_value(diff[k].get('in_dict1'))}\n"
                                        )
                                f.write("\n")

                            if updated_keys:
                                f.write("Updated Keys (grouped):\n")
                                for sec, keys_in_sec in sorted(updated_groups.items()):
                                    f.write(f"- {sec}:\n")
                                    for k in keys_in_sec:
                                        f.write(
                                            f"    - {k.split('.')[-1]}: {format_value(diff[k].get('in_dict1'))} -> {format_value(diff[k].get('in_dict2'))}\n"
                                        )
                                    f.write("\n")
                        print(f"Saved differences to {fn_human}")

                    # prepare a readable JSON diff when requested
                    if args.diff_mode in ("json", "both"):
                        fn_diff_json = os.path.join(
                            BASE_DIR, f"Response-Difference_{date_str}.json"
                        )
                        try:
                            with open(fn_diff_json, "w") as fj:
                                json.dump(diff, fj, indent=2, ensure_ascii=False)
                            print(f"Saved JSON diff to {fn_diff_json}")
                        except Exception as e:
                            print(f"Warning: failed to save JSON diff: {e}")
            print("")
            print(f">>> number of keys in old version: {nkeys_old}")
            print(f">>> number of keys in new version: {nkeys_new}")
            print("")
        else:
            print(f"File {args.fn_json} not found in current directory or {BASE_DIR}.")


if __name__ == "__main__":
    asyncio.run(run_check())
