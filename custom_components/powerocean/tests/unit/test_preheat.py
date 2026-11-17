"""test_preheat: battery preheating from the heating task report (HTTP)."""

from typing import Any

import pytest

from custom_components.powerocean.coordinator import format_minutes, preheat_of
from custom_components.powerocean.mqtt import PreheatTask

# Tasks as in real responses: PowerOcean (diagnostics of 2026-11-18, set in
# the app to 13:30-15:00) and PowerOcean Plus (issue #189, switched off)
POWEROCEAN_TASK = {
    "taskIndex": 1,
    "role": 0,
    "timeParam": 0,
    "isCfg": 2,
    "isEffect": False,
    "timeMode": 65,
    "timeTable": [58983210],
    "type": 3,
    "sysChgDsgPwr": 0,
    "isEnable": True,
}
PLUS_TASK = {
    "taskIndex": 2,
    "role": 0,
    "timeParam": 0,
    "isCfg": 2,
    "isEffect": False,
    "timeMode": 1,
    "timeTable": [45220350],
    "type": 3,
    "sysChgDsgPwr": 0,
    "isEnable": False,
}


def _response(*tasks: Any, report: str = "JTS1_ALL_HEATING_TASK_REPORT") -> dict:
    return {
        "data": {
            "quota": {report: {"timeTaskCfg": list(tasks), "updateTime": "2026-11-18"}}
        }
    }


def test_preheat_of_a_powerocean() -> None:
    assert preheat_of(_response(POWEROCEAN_TASK)) == PreheatTask(
        task_index=1, is_cfg=2, time_mode=65, enabled=True, start=810, end=900
    )


def test_preheat_of_a_powerocean_plus() -> None:
    task = preheat_of(_response(PLUS_TASK, report="RE307_ALL_HEATING_TASK_REPORT"))

    assert task == PreheatTask(
        task_index=2, is_cfg=2, time_mode=1, enabled=False, start=510, end=690
    )


def test_preheat_time_table_as_the_system_stores_it() -> None:
    task = preheat_of(_response(POWEROCEAN_TASK))

    assert task
    assert task.time_table == 58983210


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": None},
        _response(),  # never set up in the app
        _response({**POWEROCEAN_TASK, "type": 1}),  # a charge timer
        _response({**POWEROCEAN_TASK, "timeTable": []}),
        _response({**POWEROCEAN_TASK, "timeTable": ["58983210"]}),
        _response({**POWEROCEAN_TASK, "isEnable": 1}),
        _response({**POWEROCEAN_TASK, "timeMode": None}),
        # 15:00-13:30: the end before the start
        _response({**POWEROCEAN_TASK, "timeTable": [810 << 16 | 900]}),
        # end after 24:00
        _response({**POWEROCEAN_TASK, "timeTable": [1470 << 16 | 900]}),
    ],
)
def test_preheat_of_without_a_usable_task(response: dict) -> None:
    assert preheat_of(response) is None


def test_preheat_of_skips_other_tasks() -> None:
    timer = {**POWEROCEAN_TASK, "type": 1}

    task = preheat_of(_response(timer, POWEROCEAN_TASK))

    assert task
    assert (task.start, task.end) == (810, 900)


@pytest.mark.parametrize(
    ("minutes", "text"), [(0, "00:00"), (390, "06:30"), (1440, "24:00")]
)
def test_format_minutes(minutes: int, text: str) -> None:
    assert format_minutes(minutes) == text
