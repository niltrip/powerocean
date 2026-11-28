"""test_charge_window: charging windows of the app (issue #213)."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.powerocean.charge_window import format_windows
from custom_components.powerocean.coordinator import (
    charge_power_max,
    charge_tasks_of,
)
from custom_components.powerocean.mqtt import (
    ChargeTask,
    MqttCallbacks,
    PowerOceanMqtt,
    _field_bytes,
    _field_varint,
    charge_task_request,
    charge_task_switch_request,
    parse_charge_task_report,
    protobuf_fields,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SN = "SN_INVERTERBOX01"


def _task(
    index: int,
    power: int,
    time_table: tuple[int, ...],
    *,
    enabled: bool,
    mode: tuple[int, int] = (65, 0),
) -> ChargeTask:
    """Return a charging window; mode: timeMode and timeParam."""
    return ChargeTask(index, enabled, power, 1, mode[0], mode[1], time_table)


# Window 1 of a PowerOcean Plus, as the app changed it on 2026-11-28 (#213,
# capture with notes): 02:30-06:00 with 4500 W, then 02:00-06:00 with 3000 W
WINDOW_0230 = 0x0168 << 16 | 150
WINDOW_0200 = 0x0168 << 16 | 120
TASK = ChargeTask(
    task_index=1,
    enabled=True,
    power=3000,
    task_type=1,
    time_mode=65,
    time_param=0,
    time_table=(WINDOW_0200,),
)


def _header(message: bytes) -> list[tuple[int, int | bytes]]:
    ((_, header),) = protobuf_fields(message)
    return protobuf_fields(header)


def _expected_header(content: str, seq: int) -> list[tuple[int, int | bytes]]:
    """Header of the app's 96/125, field by field as captured."""
    return [
        (1, bytes.fromhex(content)),
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 125),
        (10, len(content) // 2),
        (11, 1),
        (14, seq),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


@pytest.mark.parametrize(
    ("enabled", "content"),
    [(True, "100218012001"), (False, "10021801")],  # off: field 4 left out
)
def test_switch_request_as_the_app_sends_it(
    enabled: bool,  # noqa: FBT001 (pytest parameter)
    content: str,
) -> None:
    message = charge_task_switch_request(SN, 1, enabled=enabled, seq=1234)

    assert _header(message) == _expected_header(content, 1234)


def test_charge_task_request_as_the_app_sends_it() -> None:
    # Power and time changed together in the app
    content = "100218012001300138b817404148005204f880a00b"

    assert _header(charge_task_request(SN, TASK, 77)) == _expected_header(content, 77)


def test_charge_task_request_switched_off_leaves_out_field_4() -> None:
    task = _task(1, 3000, (WINDOW_0200,), enabled=False)
    ((_, content), *_) = _header(charge_task_request(SN, task, 77))

    assert content == bytes.fromhex("100218013001" + "38b817404148005204f880a00b")


def test_charge_task_request_with_two_windows() -> None:
    task = _task(2, 1200, (WINDOW_0200, WINDOW_0230), enabled=True, mode=(129, 1037547))
    ((_, content), *_) = _header(charge_task_request(SN, task, 77))

    fields = dict(protobuf_fields(content))
    assert (fields[7], fields[8], fields[9]) == (1200, 129, 1037547)
    # Packed varints, as the system stores them
    assert fields[10] == bytes.fromhex("f880a00b9681a00b")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        # Answers to the app's query (96/126) after switching on and off
        (
            "0a17100218012001280030013894234041480052049681a00b",
            _task(1, 4500, (WINDOW_0230,), enabled=True),
        ),
        (
            "0a15100218012800300138b817404148005204f880a00b",
            _task(1, 3000, (WINDOW_0200,), enabled=False),
        ),
    ],
)
def test_parse_charge_task_report(content: str, expected: ChargeTask) -> None:
    assert parse_charge_task_report(bytes.fromhex(content)) == expected


def _report(**fields: int | bytes) -> bytes:
    values: dict[int, int | bytes] = {
        2: 2,
        3: 1,
        6: 1,
        7: 3000,
        8: 65,
        10: bytes.fromhex("f880a00b"),
    }
    values.update({int(number[1:]): value for number, value in fields.items()})
    task = b"".join(
        _field_bytes(n, v) if isinstance(v, bytes) else _field_varint(n, v)
        for n, v in values.items()
    )
    return _field_bytes(1, task)


def _packed(value: int) -> bytes:
    return bytes.fromhex(_field_varint(1, value)[1:].hex())


@pytest.mark.parametrize(
    "content",
    [
        b"",
        bytes.fromhex("1001"),  # answer to a command, no task
        _report(f6=3),  # battery preheating
        _report(f3=0),  # no taskIndex
        _report(f10=7),  # time table not packed
        _report(f10=b""),  # no window
        _report(f10=_packed(360 << 16 | 360)),  # start = end
        _report(f10=_packed(1500 << 16 | 360)),  # end after 24:00
    ],
)
def test_parse_charge_task_report_without_a_usable_task(content: bytes) -> None:
    assert parse_charge_task_report(content) is None


def test_windows_and_their_text() -> None:
    task = _task(1, 3000, (WINDOW_0200, 1440 << 16 | 1380), enabled=True)

    assert task.windows == [(120, 360), (1380, 1440)]
    assert format_windows(task) == "02:00-06:00, 23:00-24:00"


def test_window_over_midnight() -> None:
    # 22:00-06:00, as reported by a PowerOcean Plus
    task = _task(1, 1000, (23594280,), enabled=True, mode=(129, 0))

    assert task.windows == [(1320, 360)]
    assert format_windows(task) == "22:00-06:00"


# -----------------------------
# From the HTTP response
# -----------------------------


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text("utf-8"))


def test_charge_tasks_of_a_powerocean_plus() -> None:
    tasks = charge_tasks_of(_fixture("response_modified_po_plus_fw_3_0_21.json"))

    # 22:00-06:00, over midnight
    assert tasks == {1: _task(1, 1000, (23594280,), enabled=True, mode=(129, 0))}


def test_charge_tasks_of_older_responses_with_text_flags() -> None:
    # Flags as "TRUE"/"FALSE" (response of 2025)
    tasks = charge_tasks_of(_fixture("response_modified_po_dual.json"))

    assert tasks == {
        1: _task(1, 3000, (21628290,), enabled=False),
        2: _task(2, 400, (51118830,), enabled=False),
    }


def test_charge_tasks_of_keeps_time_param() -> None:
    tasks = charge_tasks_of(_fixture("response_modified_powerglow.json"))

    assert tasks == {2: _task(2, 200, (43254120,), enabled=False, mode=(129, 1037547))}


@pytest.mark.parametrize(
    "name",
    [
        "response_modified.json",
        "response_modified_po_plus.json",
        "response_synthetic_heating_rod.json",
    ],
)
def test_charge_tasks_of_without_windows(name: str) -> None:
    assert charge_tasks_of(_fixture(name)) == {}


TASK_ENTRY = {
    "taskIndex": 1,
    "role": 0,
    "timeParam": 0,
    "isCfg": 2,
    "isEffect": False,
    "timeMode": 65,
    "timeTable": [WINDOW_0200],
    "type": 1,
    "sysChgDsgPwr": 3000,
    "isEnable": True,
}


def _response(*tasks: Any) -> dict[str, Any]:
    report = {"timeTaskCfg": list(tasks), "updateTime": "2026-11-28"}
    return {"data": {"quota": {"RE307_EMS_ALL_TIMER_TASK_REPORT": report}}}


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": None},
        {"data": {"quota": {"RE307_EMS_ALL_TIMER_TASK_REPORT": None}}},
        _response({**TASK_ENTRY, "type": 3}),  # battery preheating
        _response({**TASK_ENTRY, "isCfg": 0}),  # not set up
        _response({**TASK_ENTRY, "taskIndex": 0}),
        _response({**TASK_ENTRY, "sysChgDsgPwr": None}),
        _response({**TASK_ENTRY, "isEnable": 1}),
        _response({**TASK_ENTRY, "isEnable": "yes"}),
        _response({**TASK_ENTRY, "timeTable": []}),
        _response({**TASK_ENTRY, "timeTable": [str(WINDOW_0200)]}),
        _response({**TASK_ENTRY, "timeTable": [360 << 16 | 360]}),  # start = end
        _response({**TASK_ENTRY, "timeTable": [1500 << 16 | 360]}),  # after 24:00
        _response({**TASK_ENTRY, "timeMode": True}),
    ],
)
def test_charge_tasks_of_without_a_usable_task(response: dict[str, Any]) -> None:
    assert charge_tasks_of(response) == {}


def test_charge_tasks_of_ignores_the_wallbox_timer() -> None:
    # The wallbox's charging timer has its own report
    response = {
        "data": {
            "quota": {
                "RE307_EV_CHARGING_TIMER_TASK_REPORT": {"timeTaskCfg": [TASK_ENTRY]}
            }
        }
    }

    assert charge_tasks_of(response) == {}


@pytest.mark.parametrize(
    ("sn", "maximum"),
    [
        ("R371XXXXXXXXXXXX", 29900),  # PowerOcean Plus
        ("HJ31XXXXXXXXXXXX", 10000),  # PowerOcean
        ("HJ35XXXXXXXXXXXX", 6000),
        ("HJ37XXXXXXXXXXXX", 12000),
        ("HJ39XXXXXXXXXXXX", 10000),  # unknown model of the family
        ("J32BXXXXXXXXXXXX", 6000),  # single phase
        ("SN_INVERTERBOX01", 10000),  # unknown
    ],
)
def test_charge_power_max(sn: str, maximum: int) -> None:
    assert charge_power_max(sn) == maximum


# -----------------------------
# MQTT messages
# -----------------------------

USER_ID = "1234567890"
CREDENTIALS = {
    "url": "mqtt.example.com",
    "port": "8883",
    "certificateAccount": "app-account",
    "certificatePassword": "app-password",
}
SET_REPLY = f"/app/{USER_ID}/{SN}/thing/property/set_reply"


def _connection() -> PowerOceanMqtt:
    callbacks = MqttCallbacks(
        on_modules=MagicMock(),
        on_settings=MagicMock(),
        on_ack=MagicMock(),
        on_energy_stream=MagicMock(),
        on_preheat=MagicMock(),
        on_charge_task=MagicMock(),
    )
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, callbacks)
    mqtt.client = MagicMock()
    return mqtt


def _message(cmd_id: int, content: str, seq: int) -> bytes:
    header = b"".join(
        (
            _field_bytes(1, bytes.fromhex(content)),
            _field_varint(2, 96),
            _field_varint(3, 32),
            _field_varint(8, 96),
            _field_varint(9, cmd_id),
            _field_varint(14, seq),
        )
    )
    return _field_bytes(1, header)


def _receive(mqtt: PowerOceanMqtt, topic: str, payload: bytes) -> None:
    mqtt._on_message(mqtt.client, None, MagicMock(topic=topic, payload=payload))


def test_answer_to_a_query_hands_over_the_window() -> None:
    mqtt = _connection()

    _receive(
        mqtt,
        SET_REPLY,
        _message(126, "0a15100218012800300138b817404148005204f880a00b", 55),
    )

    mqtt.callbacks.on_ack.assert_called_once_with(55)
    mqtt.callbacks.on_charge_task.assert_called_once_with(
        _task(1, 3000, (WINDOW_0200,), enabled=False)
    )


def test_answer_to_a_command_is_only_an_acknowledgement() -> None:
    mqtt = _connection()

    _receive(mqtt, SET_REPLY, _message(125, "08011001", 56))

    mqtt.callbacks.on_ack.assert_called_once_with(56)
    mqtt.callbacks.on_charge_task.assert_not_called()


def test_set_charge_task_publishes_the_commands() -> None:
    mqtt = _connection()

    mqtt.set_charge_task_enabled(1, 0, seq=12)
    mqtt.set_charge_task(TASK, seq=13)

    topic = f"/app/{USER_ID}/{SN}/thing/property/set"
    assert [c.args for c in mqtt.client.publish.call_args_list] == [
        (topic, charge_task_switch_request(SN, 1, enabled=False, seq=12)),
        (topic, charge_task_request(SN, TASK, 13)),
    ]
