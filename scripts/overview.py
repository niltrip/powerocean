# ruff: noqa: INP001 (a script, not a package)
"""
Show how an API response appears in Home Assistant: devices and entities.

Parses the response with the integration's parser and writes a Markdown
overview: the device tree, then per device its entities in the sections
Home Assistant uses (sensors, diagnostic, disabled) with name, key, value
and unit. Names and states are translated as Home Assistant shows them
(German by default, --language en for English).

Usage (from the repository root):
    scripts/overview response_modified_po_dual.json
    scripts/overview path/to/diagnostics.json --variant 87 --sn SN_MAIN
    scripts/overview response_modified.json -o overview.md
    scripts/overview response_modified.json --language en

A bare file name is looked up in tests/fixtures. A diagnostics download
works too: its anonymized API response is used, with the variant and the
serial number placeholder SN_MAIN from the download.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "powerocean"
FIXTURES = COMPONENT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from custom_components.powerocean.const import DEFAULT_NAME  # noqa: E402
from custom_components.powerocean.parse import parse  # noqa: E402
from custom_components.powerocean.types import PowerOceanEndPoint  # noqa: E402


def _load(name: str) -> tuple[dict[str, Any], str | None, str | None]:
    """Return the API response, and variant and serial number if known."""
    path = Path(name)
    if not path.exists() and (FIXTURES / name).exists():
        path = FIXTURES / name
    data = json.loads(path.read_text("utf-8"))
    diagnostics = data.get("data")
    if isinstance(diagnostics, dict) and "api_response" in diagnostics:
        variant = (diagnostics.get("coordinator") or {}).get("variant")
        return diagnostics["api_response"], variant, "SN_MAIN"
    return data, None, None


def _translations(language: str) -> dict[str, Any]:
    """Return the sensor translations (name, state) by translation key."""
    path = COMPONENT / "translations" / f"{language}.json"
    return json.loads(path.read_text("utf-8"))["entity"]["sensor"]


def _name(endpoint: PowerOceanEndPoint, translations: dict[str, Any]) -> str:
    """Return the name Home Assistant shows: translated, else the key."""
    if endpoint.named and endpoint.translation_key in translations:
        return translations[endpoint.translation_key]["name"]
    return endpoint.friendly_name


def _value(
    endpoint: PowerOceanEndPoint, value: Any, translations: dict[str, Any]
) -> str:
    device_class = endpoint.cls[0] if endpoint.cls else None
    if device_class == "timestamp" and isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    if value is None:
        return "-"
    if endpoint.translated_states:
        # Like sensor.py: translated states are lowercase
        states = translations.get(endpoint.translation_key, {}).get("state", {})
        return states.get(str(value).lower(), str(value).lower())
    return str(value)


def _report(endpoint: PowerOceanEndPoint) -> str:
    """Return the report part of the unique_id ({sn}_{report}_{key})."""
    report = endpoint.internal_unique_id.removeprefix(f"{endpoint.serial}_")
    return report.removesuffix(endpoint.friendly_name).rstrip("_") or "data"


def _section(endpoint: PowerOceanEndPoint) -> str:
    """Return the section of the device page, as sensor.py decides it."""
    if not endpoint.enabled_default:
        return "Deaktiviert"
    unit = endpoint.cls[1] if endpoint.cls else None
    if unit is None or endpoint.diagnostic:
        return "Diagnose"
    return "Sensoren"


def overview(
    response: dict[str, Any], variant: str, sn: str, title: str, language: str
) -> str:
    """Return the Markdown overview of a response."""
    translations = _translations(language)
    result = parse(response, sn, variant)
    values = result.values()
    endpoints = [PowerOceanEndPoint.from_datapoint(d) for d in result.first().values()]

    devices: dict[str, dict[str, Any]] = {}
    entities: dict[str, list[PowerOceanEndPoint]] = defaultdict(list)
    for endpoint in endpoints:
        info = endpoint.device_info or {}
        device_sn = endpoint.serial
        # Like the device registry: each entity updates name and model, so
        # the last one wins; a missing via_device keeps the known one
        device = devices.setdefault(device_sn, {"via": None})
        device["name"] = info.get("name")
        device["model"] = info.get("model")
        if via := info.get("via_device"):
            device["via"] = via[1]
        entities[device_sn].append(endpoint)

    # The main device is named after the config entry (__init__.py)
    if sn in devices:
        devices[sn]["name"] = DEFAULT_NAME

    lines = [f"# Übersicht: {title}", ""]
    sections = defaultdict(int)
    for endpoint in endpoints:
        sections[_section(endpoint)] += 1
    lines += [
        f"Variante {variant}, Seriennummer {sn}: {len(endpoints)} Entities auf "
        f"{len(devices)} Geräten ("
        + ", ".join(f"{count} {name}" for name, count in sorted(sections.items()))
        + ")",
        "",
        "## Geräte",
        "",
    ]

    # Devices without a known parent are roots of the tree
    children: dict[str | None, list[str]] = defaultdict(list)
    for device_sn, device in devices.items():
        parent = device["via"] if device["via"] in devices else None
        children[parent].append(device_sn)

    def tree(parent: str | None, depth: int) -> None:
        for device_sn in children[parent]:
            device = devices[device_sn]
            lines.append(
                f"{'  ' * depth}- **{device['name']}** - {device['model']}, "
                f"{len(entities[device_sn])} Entities"
            )
            tree(device_sn, depth + 1)

    tree(None, 0)

    for device_sn, device in devices.items():
        lines += ["", f"## {device['name']} ({device_sn})"]
        by_section: dict[str, list[PowerOceanEndPoint]] = defaultdict(list)
        for endpoint in entities[device_sn]:
            by_section[_section(endpoint)].append(endpoint)
        for section in ("Sensoren", "Diagnose", "Deaktiviert"):
            if not by_section[section]:
                continue
            lines += [
                "",
                f"### {section} ({len(by_section[section])})",
                "",
                "| Name | Key | Wert | Einheit | Report |",
                "|---|---|---|---|---|",
            ]
            rows = sorted(
                (_name(endpoint, translations), endpoint)
                for endpoint in by_section[section]
            )
            for name, endpoint in rows:
                unit = (endpoint.cls[1] if endpoint.cls else None) or ""
                value = _value(
                    endpoint, values.get(endpoint.internal_unique_id), translations
                )
                lines.append(
                    f"| {name} | {endpoint.friendly_name} | {value} | {unit} | "
                    f"{_report(endpoint)} |"
                )
    return "\n".join(lines) + "\n"


def main() -> None:
    """Parse the arguments and write the overview."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("response", help="API response (JSON) or fixture name")
    parser.add_argument("--variant", help="83 PowerOcean (default), 85 DC fit, 87 Plus")
    parser.add_argument(
        "--sn", help="serial number of the config entry (default SN_INVERTERBOX01)"
    )
    parser.add_argument(
        "--language", choices=("de", "en"), default="de", help="default de"
    )
    parser.add_argument("-o", "--output", help="write to this file instead")
    args = parser.parse_args()

    response, variant, sn = _load(args.response)
    text = overview(
        response,
        args.variant or variant or "83",
        args.sn or sn or "SN_INVERTERBOX01",
        Path(args.response).name,
        args.language,
    )
    if args.output:
        Path(args.output).write_text(text, "utf-8")
        print(f"Geschrieben: {args.output}")  # noqa: T201
    else:
        print(text)  # noqa: T201


if __name__ == "__main__":
    main()
