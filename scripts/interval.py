# ruff: noqa: INP001, T201 (a script, not a package; prints its result)
"""
Measure how often the EcoFlow cloud updates the PowerOcean data.

Fetches the device detail response every few seconds and records, per
report, when its content and its updateTime change. The summary shows the
typical time between changes, so the scan interval can be chosen to match.

    scripts/interval                     15 minutes, every 5 seconds
    scripts/interval --minutes 30 --every 3

Credentials are asked for and not stored. The CSV in measurements/ (not
committed) contains report names and times only, no serial numbers.
Ctrl+C stops early and still prints the summary.
"""

import argparse
import asyncio
import contextlib
import csv
import getpass
import hashlib
import itertools
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.powerocean.api import (  # noqa: E402
    EcoflowApi,
    EcoflowApiError,
    EcoflowConnectionError,
)
from custom_components.powerocean.devices import MODEL_NAME_MAP  # noqa: E402

OUT_DIR = ROOT / "measurements"


@dataclass
class ReportLog:
    """What was seen of one report during the measurement."""

    digest: str | None = None
    update_time: str | None = None
    # Poll times (monotonic seconds) at which content / updateTime changed
    content_changes: list[float] = field(default_factory=list)
    time_changes: list[float] = field(default_factory=list)


def reports_of(data: dict[str, Any]) -> dict[str, Any]:
    """
    Return the reports of a response by name, without serial numbers.

    The flat values directly under "data" count as report "data". In dual
    systems, the reports of each inverter are prefixed "inverter 1/" etc.
    """
    flat = {k: v for k, v in data.items() if k not in ("quota", "parallel")}
    reports: dict[str, Any] = {"data": flat}
    if isinstance(data.get("quota"), dict):
        reports.update(data["quota"])
    if isinstance(data.get("parallel"), dict):
        for index, inverter in enumerate(data["parallel"].values(), start=1):
            if isinstance(inverter, dict):
                for name, report in inverter.items():
                    reports[f"inverter {index}/{name}"] = report
    return reports


def digest(report: Any) -> str:
    """Return a fingerprint of a report's content, ignoring its updateTime."""
    if isinstance(report, dict):
        report = {k: v for k, v in report.items() if k != "updateTime"}
    text = json.dumps(report, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def gaps(times: list[float]) -> list[float]:
    """Return the seconds between consecutive changes."""
    return [b - a for a, b in itertools.pairwise(times)]


def describe(seconds: list[float]) -> str:
    """Return median, min and max seconds, or a dash without values."""
    if not seconds:
        return "-"
    return (
        f"{statistics.median(seconds):5.0f} s ({min(seconds):.0f}-{max(seconds):.0f})"
    )


async def login(api: EcoflowApi) -> None:
    """Log in, retrying while the cloud is not reachable."""
    while True:
        try:
            await api.async_authorize()
        except EcoflowConnectionError as err:
            print(f"Anmeldung: {err}, neuer Versuch in 10 s")
            await asyncio.sleep(10)
        else:
            return


async def measure(args: argparse.Namespace, api: EcoflowApi, out: Path) -> None:
    """Poll the API and write one CSV row per report and poll."""
    logs: dict[str, ReportLog] = defaultdict(ReportLog)
    durations: list[float] = []
    failures = 0
    start = time.monotonic()
    end = start + args.minutes * 60

    await login(api)
    print(f"Angemeldet ({api.api_host}). Messung läuft, Abbruch mit Strg+C.\n")

    with out.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            ["poll_time", "seconds", "report", "content_changed", "updateTime"]
        )
        try:
            while (now := time.monotonic()) < end:
                poll_time = datetime.now().astimezone().strftime("%H:%M:%S")
                try:
                    response = await api.fetch_raw()
                except EcoflowConnectionError as err:
                    failures += 1
                    print(f"{poll_time}  Fehler: {err}")
                except EcoflowApiError as err:
                    failures += 1
                    print(f"{poll_time}  Fehler: {err}, melde neu an")
                    await login(api)
                else:
                    durations.append(time.monotonic() - now)
                    changed = []
                    for name, report in reports_of(response["data"]).items():
                        log = logs[name]
                        new_digest = digest(report)
                        update_time = (
                            report.get("updateTime")
                            if isinstance(report, dict)
                            else None
                        )
                        # The first poll is no change
                        content_changed = (
                            log.digest is not None and new_digest != log.digest
                        )
                        if content_changed:
                            log.content_changes.append(now)
                            changed.append(name)
                        if log.update_time is not None and (
                            update_time != log.update_time
                        ):
                            log.time_changes.append(now)
                        log.digest = new_digest
                        log.update_time = update_time
                        writer.writerow(
                            [
                                poll_time,
                                f"{now - start:.1f}",
                                name,
                                int(content_changed),
                                update_time or "",
                            ]
                        )
                    file.flush()
                    print(f"{poll_time}  geändert: {', '.join(changed) or '-'}")
                await asyncio.sleep(max(0.0, now + args.every - time.monotonic()))
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nAbgebrochen.")

    summary(logs, durations, failures, time.monotonic() - start, args.every)


def summary(
    logs: dict[str, ReportLog],
    durations: list[float],
    failures: int,
    elapsed: float,
    every: float,
) -> None:
    """Print the time between changes per report."""
    print(
        f"\nDauer {elapsed / 60:.1f} min, Abfrage alle {every:g} s, "
        f"{len(durations)} Antworten, {failures} Fehler, "
        f"Antwortzeit {statistics.median(durations) if durations else 0:.1f} s"
    )
    print("Zeit zwischen Änderungen: Median (Minimum-Maximum)\n")
    width = max((len(name) for name in logs), default=10)
    print(f"{'Report':<{width}}  {'Änderungen':>10}  {'Inhalt':<18}  updateTime")
    for name in sorted(logs, key=lambda n: (-len(logs[n].content_changes), n)):
        log = logs[name]
        print(
            f"{name:<{width}}  {len(log.content_changes):>10}  "
            f"{describe(gaps(log.content_changes)):<18}  "
            f"{describe(gaps(log.time_changes))}"
        )


def main() -> None:
    """Ask for credentials and run the measurement."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--minutes", type=float, default=15, help="Dauer")
    parser.add_argument("--every", type=float, default=5, help="Sekunden")
    args = parser.parse_args()

    serial = input("Seriennummer des Wechselrichters: ").strip()
    username = input("E-Mail des EcoFlow-Kontos: ").strip()
    password = getpass.getpass("Passwort: ")
    models = ", ".join(f"{code} {name}" for code, name in MODEL_NAME_MAP.items())
    variant = input(f"Modell ({models}) [83]: ").strip() or "83"

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"interval-{datetime.now().astimezone():%Y%m%d-%H%M%S}.csv"
    api = EcoflowApi(serial, username, password, variant)

    async def run() -> None:
        try:
            await measure(args, api, out)
        finally:
            await api.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
    print(f"\nRohdaten: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
