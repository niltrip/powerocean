# ruff: noqa: INP001, T201 (a script, not a package; prints its result)
"""
Readable entity names from scripts/entity_names.csv.

The table is the only place to edit names. This script writes them into
strings.json, translations/en.json, translations/de.json and entity_names.py,
and adds a row (without names) for every datapoint key of the fixtures that
the table does not list yet.

    scripts/names            update the table and the generated files
    scripts/names --check    only report what is out of date (exit code 1)

Columns of the table (separated by ";"):
    group    for sorting only
    key      datapoint key, e.g. bpSoc
    context  empty, or where the name differs: "pack" (battery pack),
             "inverter" (one inverter of a dual system)
    en, de   name; empty: the key stays the name
    note     for sorting only
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.powerocean.datapoints import (  # noqa: E402
    NAME_CONTEXTS,
    TRANSLATED_STATES,
    translation_key,
)
from custom_components.powerocean.parse import parse  # noqa: E402

TABLE = ROOT / "scripts" / "entity_names.csv"
COMPONENT = ROOT / "custom_components" / "powerocean"
FIXTURES = COMPONENT / "tests" / "fixtures"
COLUMNS = ["group", "key", "context", "en", "de", "note"]
# Translation file -> language of its names
LANGUAGES = {
    COMPONENT / "strings.json": "en",
    COMPONENT / "translations" / "en.json": "en",
    COMPONENT / "translations" / "de.json": "de",
}
NAMES_MODULE = COMPONENT / "entity_names.py"
# Model of the fixtures that are not a PowerOcean (83)
VARIANTS = {"dcfit": "85", "po_plus": "87", "single": "86"}


def read_table() -> list[dict[str, str]]:
    """Return the rows of the table, without surrounding blanks."""
    with TABLE.open(encoding="utf-8", newline="") as file:
        return [
            {column: (row.get(column) or "").strip() for column in COLUMNS}
            for row in csv.DictReader(file, delimiter=";")
        ]


def table_text(rows: list[dict[str, str]]) -> str:
    """Return the table as written to the file."""
    lines = [";".join(COLUMNS)]
    lines += [";".join(row[column] for column in COLUMNS) for row in rows]
    return "\n".join(lines) + "\n"


def fixture_keys() -> dict[str, str]:
    """Return every datapoint key of the fixtures, with its device's model."""
    keys: dict[str, str] = {}
    for path in sorted(FIXTURES.glob("response*.json")):
        response = json.loads(path.read_text("utf-8"))
        variant = next((v for k, v in VARIANTS.items() if k in path.name), "83")
        for datapoint in parse(response, "SN_INVERTERBOX01", variant).datapoints:
            keys.setdefault(datapoint.key, datapoint.device.model)
    return keys


def add_missing(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return rows without names for the fixture keys the table lacks."""
    listed = {row["key"] for row in rows}
    return [
        dict.fromkeys(COLUMNS, "") | {"group": f"Neu: {device}", "key": key}
        for key, device in fixture_keys().items()
        if key not in listed
    ]


def names(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    """Return the names by (context, key), only rows with an English name."""
    return {(row["context"], row["key"]): row for row in rows if row["en"]}


def name_translation_key(context: str, key: str) -> str:
    """Return the translation key; the same as types._name_translation_key."""
    return f"{translation_key(key)}_{context}" if context else translation_key(key)


def problems(rows: list[dict[str, str]]) -> list[str]:
    """Return the mistakes in the table, which block writing the files."""
    found = []
    seen: dict[str, tuple[str, str]] = {}
    contexts = {"", *NAME_CONTEXTS.values()}
    for row in rows:
        context, key = row["context"], row["key"]
        if not key:
            found.append(f"Zeile ohne Schlüssel: {row}")
            continue
        if context not in contexts:
            found.append(f"{key}: unbekannter Kontext {context!r}")
        if context and key in TRANSLATED_STATES:
            found.append(f"{key}: übersetzte Zustände gibt es nur ohne Kontext")
        if row["de"] and not row["en"]:
            found.append(f"{key}: deutscher Name ohne englischen")
        if not row["en"]:
            continue
        name_key = name_translation_key(context, key)
        if name_key in seen and seen[name_key] != (context, key):
            found.append(f"{key}: Übersetzungsschlüssel {name_key} doppelt")
        seen[name_key] = (context, key)
    return found


def translations_text(path: Path, rows: list[dict[str, str]]) -> str:
    """Return the translation file with the names of the table."""
    language = LANGUAGES[path]
    text = path.read_text("utf-8")
    data = json.loads(text)
    # Keep the indentation of the file
    second_line = text.split("\n", 2)[1]
    indent = len(second_line) - len(second_line.lstrip(" ")) or 2
    sensors = data.setdefault("entity", {}).setdefault("sensor", {})
    wanted = {
        name_translation_key(context, key): row[language]
        for (context, key), row in names(rows).items()
        if row[language]
    }
    for name_key, entry in list(sensors.items()):
        entry.pop("name", None)
        if not entry:
            del sensors[name_key]
    for name_key, name in wanted.items():
        # Name first, then the states
        sensors[name_key] = {"name": name, **sensors.get(name_key, {})}
    data["entity"]["sensor"] = dict(sorted(sensors.items()))
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def module_text(rows: list[dict[str, str]]) -> str:
    """Return entity_names.py, the named datapoints for the integration."""
    entries = "".join(
        f'        ("{context}", "{key}"),\n' for context, key in sorted(names(rows))
    )
    return (
        '"""\n'
        "entity_names.py: Datapoints with a readable name.\n"
        "\n"
        "Generated by scripts/names from scripts/entity_names.csv, do not edit.\n"
        "The names are in strings.json and translations/*.json.\n"
        '"""\n'
        "\n"
        '# (context, key) of each named datapoint; context "" applies where no\n'
        "# other context matches (see datapoints.name_context)\n"
        "NAMED: frozenset[tuple[str, str]] = frozenset(\n"
        "    {\n"
        f"{entries}"
        "    }\n"
        ")\n"
    )


def main() -> int:
    """Update or check the table and the generated files."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument(
        "--check", action="store_true", help="nur prüfen, nichts schreiben"
    )
    args = parser.parse_args()

    rows = read_table()
    new_rows = add_missing(rows)
    rows += new_rows
    found = problems(rows)
    for problem in found:
        print(f"Fehler: {problem}")
    if found:
        return 1

    outputs = {TABLE: table_text(rows), NAMES_MODULE: module_text(rows)}
    outputs |= {path: translations_text(path, rows) for path in LANGUAGES}
    changed = [
        path
        for path, text in outputs.items()
        if not path.exists() or path.read_text("utf-8") != text
    ]
    for path in changed:
        state = "veraltet" if args.check else "geschrieben"
        print(f"{state}: {path.relative_to(ROOT)}")
        if not args.check:
            path.write_text(outputs[path], encoding="utf-8")
    if new_rows:
        print(f"{len(new_rows)} neue Schlüssel ohne Namen in der Tabelle")
    named = len(names(rows))
    print(f"{named} von {len(rows)} Zeilen mit Namen")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
