"""test_zone: sysZone from the time zone of Home Assistant (issue #196)."""

from collections.abc import Generator

import pytest
from freezegun import freeze_time
from homeassistant.util import dt as dt_util

from custom_components.powerocean.coordinator import zone_of_time_zone


@pytest.fixture(autouse=True)
def restore_time_zone() -> Generator[None]:
    before = dt_util.get_default_time_zone()
    yield
    dt_util.set_default_time_zone(before)


@pytest.mark.parametrize(
    ("time_zone", "now", "expected"),
    [
        # As the app sent it in France in November (issue #196)
        ("Europe/Paris", "2026-11-22T15:57:19+00:00", 4),
        # Summer time: UTC+2, as captured before 2026-10-25
        ("Europe/Berlin", "2026-10-24T12:00:00+00:00", 8),
        ("UTC", "2026-11-22T12:00:00+00:00", 0),
        # UTC+5:30 and UTC+10
        ("Asia/Kolkata", "2026-11-22T12:00:00+00:00", 22),
        ("Australia/Brisbane", "2026-11-22T12:00:00+00:00", 40),
        # West of UTC: how the app sends it is not known
        ("America/New_York", "2026-11-22T12:00:00+00:00", None),
    ],
)
def test_zone_of_time_zone(time_zone: str, now: str, expected: int | None) -> None:
    dt_util.set_default_time_zone(dt_util.get_time_zone(time_zone))

    with freeze_time(now):
        assert zone_of_time_zone() == expected
