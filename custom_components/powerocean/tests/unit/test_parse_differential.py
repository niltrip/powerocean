"""
Differential test: the new parser (parse.py) against the old one (parser.py).

Both parsers get the same responses: every fixture, plus variants with
missing reports, wrong types and randomly damaged values. They must yield
the same entities (unique_id, order, name, device, unit, icon), the same
values and the same active errors - or both fail with the same exception.

Remove this test together with parser.py once the new parser is in use.
"""

import copy
import json
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from custom_components.powerocean.parse import parse
from custom_components.powerocean.parser import EcoflowParser
from custom_components.powerocean.types import PowerOceanEndPoint

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SN = "SN_INVERTERBOX01"

API_FIXTURES = [
    ("response_modified.json", "83"),
    ("response_modified_dcfit_2025.json", "85"),
    ("response_modified_po_dual.json", "83"),
    ("response_modified_po_plus.json", "87"),
    ("response_modified_po_plus_feature.json", "87"),
    ("response_modified_error_521.json", "83"),
    ("response_synthetic_heating_rod.json", "83"),
    ("response_synthetic_heating_rod_dual.json", "83"),
]

BAD_VALUES = [None, "", "x", "nan", [], {}, -1.5, 0, True, "U05fWDAx", "{"]
RANDOM_CASES = 300


# -----------------------------
# Running both parsers
# -----------------------------


def _old(response: dict, variant: str) -> Any:
    parser = EcoflowParser(variant=variant, sn=SN)
    # Like the coordinator: structure and values from the same response
    structure = parser.parse_structure(response)
    values = parser.parse_values(response)
    return structure, values, parser.active_errors


def _new(response: dict, variant: str) -> Any:
    result = parse(response, SN, variant)
    structure = {
        unique_id: PowerOceanEndPoint.from_datapoint(datapoint)
        for unique_id, datapoint in result.first().items()
    }
    return structure, result.values(), result.active_errors


def _run(func: Any, response: dict, variant: str) -> Any:
    try:
        return func(copy.deepcopy(response), variant)
    except Exception as err:  # noqa: BLE001 (any error must match)
        return type(err)


def _comparable(outcome: Any) -> Any:
    """Make outcomes comparable, with NaN equal to NaN."""
    if isinstance(outcome, type):
        return outcome
    structure, values, errors = outcome
    return (
        list(structure.items()),
        repr(list(values.items())),
        errors,
    )


def _difference(old: Any, new: Any) -> str:
    if isinstance(old, type) or isinstance(new, type):
        return f"old: {old}, new: {new}"
    old_structure, new_structure = old[0], new[0]
    if list(old_structure) != list(new_structure):
        missing = [u for u in old_structure if u not in new_structure][:3]
        extra = [u for u in new_structure if u not in old_structure][:3]
        return f"unique_ids differ: missing {missing}, extra {extra}"
    for unique_id, endpoint in old_structure.items():
        if endpoint != new_structure[unique_id]:
            return f"{unique_id}: {endpoint} != {new_structure[unique_id]}"
    if repr(list(old[1].items())) != repr(list(new[1].items())):
        diff = [
            (u, v, new[1].get(u))
            for u, v in old[1].items()
            if repr(v) != repr(new[1].get(u))
        ]
        return f"values differ: {diff[:3]}"
    return "active errors differ"


# -----------------------------
# Damaged variants of a response
# -----------------------------


def _report_containers(response: dict) -> list[dict]:
    data = response.get("data") or {}
    containers = [data["quota"]] if isinstance(data.get("quota"), dict) else []
    parallel = data.get("parallel")
    if isinstance(parallel, dict):
        containers += [p for p in parallel.values() if isinstance(p, dict)]
    return containers


def _systematic(response: dict) -> Iterator[tuple[str, dict]]:
    yield "unchanged", response
    for bad in (None, [], "x"):
        damaged = copy.deepcopy(response)
        damaged["data"] = bad
        yield f"data = {bad!r}", damaged
    for key in ("quota", "parallel"):
        for bad in (None, {}, [], "x"):
            damaged = copy.deepcopy(response)
            if isinstance(damaged.get("data"), dict) and key in damaged["data"]:
                damaged["data"][key] = bad
                yield f"{key} = {bad!r}", damaged

    for index, container in enumerate(_report_containers(response)):
        for report in list(container):
            damaged = copy.deepcopy(response)
            del _report_containers(damaged)[index][report]
            yield f"{report} missing", damaged
            for bad in BAD_VALUES:
                damaged = copy.deepcopy(response)
                _report_containers(damaged)[index][report] = bad
                yield f"{report} = {bad!r}", damaged


def _paths(data: Any, path: tuple = ()) -> Iterator[tuple]:
    """Yield the path of every value, also inside embedded JSON strings."""
    if isinstance(data, dict):
        for key, value in data.items():
            yield (*path, key)
            yield from _paths(value, (*path, key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield (*path, index)
            yield from _paths(value, (*path, index))
    elif isinstance(data, str) and data.lstrip().startswith("{"):
        try:
            yield from _paths(json.loads(data), (*path, "<json>"))
        except ValueError:
            return


def _apply(data: Any, path: tuple, bad: Any, *, delete: bool) -> Any:
    """Return `data` with the value at `path` replaced or deleted."""
    if path[0] == "<json>":
        return json.dumps(_apply(json.loads(data), path[1:], bad, delete=delete))
    key, rest = path[0], path[1:]
    if rest:
        data[key] = _apply(data[key], rest, bad, delete=delete)
    elif delete and isinstance(data, dict):
        del data[key]
    else:
        data[key] = bad
    return data


def _random(response: dict, seed: int) -> Iterator[tuple[str, dict]]:
    rng = random.Random(seed)  # noqa: S311 (reproducible test data)
    paths = list(_paths(response))
    for case in range(RANDOM_CASES):
        damaged = copy.deepcopy(response)
        changes = []
        for path in rng.sample(paths, k=rng.randint(1, 3)):
            bad = rng.choice(BAD_VALUES)
            delete = rng.random() < 0.3
            try:
                damaged = _apply(damaged, path, copy.deepcopy(bad), delete=delete)
            except (KeyError, IndexError, TypeError, ValueError):
                continue  # path vanished by an earlier change
            change = "deleted" if delete else f"= {bad!r}"
            changes.append(f"{'/'.join(map(str, path))} {change}")
        yield f"random {case}: {'; '.join(changes)}", damaged


@pytest.mark.parametrize(("fixture_file_name", "variant"), API_FIXTURES)
def test_new_parser_matches_old(fixture_file_name: str, variant: str) -> None:
    response = json.loads((FIXTURES_DIR / fixture_file_name).read_text("utf-8"))
    seed = sum(map(ord, fixture_file_name))

    failures = []
    cases = 0
    for description, case in (*_systematic(response), *_random(response, seed)):
        cases += 1
        old = _run(_old, case, variant)
        new = _run(_new, case, variant)
        if _comparable(old) != _comparable(new):
            failures.append(f"{description}: {_difference(old, new)}")

    assert not failures, f"{len(failures)} of {cases} cases differ:\n" + "\n".join(
        failures[:10]
    )


def test_unknown_variant_fails_like_before() -> None:
    response = json.loads((FIXTURES_DIR / "response_modified.json").read_text("utf-8"))
    assert _run(_old, response, "default") is _run(_new, response, "default")
