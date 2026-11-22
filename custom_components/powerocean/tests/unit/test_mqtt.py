"""test_mqtt: protobuf messages and module info of the MQTT connection."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.powerocean.mqtt import (
    ModuleVersion,
    MqttCallbacks,
    Part,
    PowerOceanMqtt,
    PreheatTask,
    _field_bytes,
    _field_varint,
    battery_save_mode_request,
    energy_stream,
    energy_stream_request,
    get_all_request,
    led_brightness_request,
    module_info_request,
    module_versions,
    parse_module_info,
    parse_preheat_report,
    preheat_get_request,
    preheat_request,
    protobuf_fields,
    soc_limits_request,
    split_message,
)

SN = "SN_INVERTERBOX01"
USER_ID = "1234567890"
CREDENTIALS = {
    "url": "mqtt.example.com",
    "port": "8883",
    "certificateAccount": "app-account",
    "certificatePassword": "app-password",
}

# get_reply of a get-all, captured on 2026-10-25: the module info (53/113)
# and a report of another command set (96/1, content replaced). Serial numbers
# of the modules (fields 3 and 10) and of the header (25) are removed.
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
GET_REPLY = bytes.fromhex((FIXTURES_DIR / "mqtt_module_info.hex").read_text())

MODULES = {
    96: ModuleVersion(96, 1, "5.1.38.5", "5.1.0.1"),  # EMS
    4: ModuleVersion(4, 2, "5.1.23.17", "2.2.0.1"),  # PCS
    53: ModuleVersion(53, 4, "4.1.13.15", "2.0.0.4"),  # IoT/Wi-Fi
    50: ModuleVersion(50, 5, "2.0.11.3"),
    106: ModuleVersion(106, 8, "1.0.0.0"),  # smart meter
}


def _header(message: bytes) -> list[tuple[int, int | bytes]]:
    """Return the fields of the only header of a message, seq left out."""
    ((number, header),) = protobuf_fields(message)
    assert number == 1
    assert isinstance(header, bytes)
    return [(key, value) for key, value in protobuf_fields(header) if key != 14]


def test_get_all_request_as_the_app_sends_it() -> None:
    # Field by field as captured from the Android app (seq is a timestamp)
    assert _header(get_all_request()) == [
        (2, 32),
        (3, 32),
        (23, b"Android"),
        (26, b""),
        (27, b""),
    ]


def test_module_info_request_as_the_app_sends_it() -> None:
    assert _header(module_info_request(SN)) == [
        (2, 32),
        (3, 53),
        (4, 1),
        (5, 1),
        (8, 53),
        (9, 113),
        (11, 1),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


def test_led_brightness_request_as_the_app_sends_it() -> None:
    message = led_brightness_request(SN, 40, seq=1234)
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app (40 %)
    assert protobuf_fields(header) == [
        (1, bytes.fromhex("08011028")),  # field 1 = 1, field 2 = 40
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 119),
        (10, 4),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


def test_soc_limits_request_as_the_app_sends_it() -> None:
    message = soc_limits_request(
        SN, charge_limit=100, backup_reserve=11, dev_soc=100, seq=1234
    )
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app (backup reserve 11 %)
    assert protobuf_fields(header) == [
        (1, bytes.fromhex("0864100b2064")),  # fields 1 = 100, 2 = 11, 4 = 100
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 112),
        (10, 6),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


def test_battery_save_mode_request_as_the_app_sends_it() -> None:
    message = battery_save_mode_request(SN, enabled=0, zone=4, seq=1234)
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app (save mode off)
    assert protobuf_fields(header) == [
        (1, bytes.fromhex("080010041800")),  # fields 1 = 0, 2 = 4, 3 = 0
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 138),
        (10, 6),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


# Battery preheating as the app set it on 2026-11-17 (capture with notes):
# window in minutes and enabled, content of the app's 96/201 command
PREHEAT_COMMANDS = [
    (360, 450, True, "1002180120013003404148005204e882880e"),  # 06:00-07:30
    (390, 1440, True, "10021801200130034041480052048683802d"),  # 06:30-24:00
    (390, 510, True, "10021801200130034041480052048683f80f"),  # 06:30-08:30
    (0, 510, True, "10021801200130034041480052048080f80f"),  # 00:00-08:30
    (0, 510, False, "1002180130034041480052048080f80f"),  # switched off
]


@pytest.mark.parametrize(("start", "end", "enabled", "content"), PREHEAT_COMMANDS)
def test_preheat_request_as_the_app_sends_it(
    start: int,
    end: int,
    enabled: bool,  # noqa: FBT001 (pytest parameter)
    content: str,
) -> None:
    task = PreheatTask(
        task_index=1, is_cfg=2, time_mode=65, enabled=enabled, start=start, end=end
    )
    message = preheat_request(SN, task, seq=1234)
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app
    assert protobuf_fields(header) == [
        (1, bytes.fromhex(content)),
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 201),
        (10, len(content) // 2),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


def test_preheat_get_request_as_the_app_sends_it() -> None:
    message = preheat_get_request(SN, seq=1234)
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app: no pdata, no data_len
    assert protobuf_fields(header) == [
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 203),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


# Answers to the preheating query (96/203) as captured on 2026-11-17: the
# task in field 1, field 4 only when on
PREHEAT_REPORT_ON = _field_bytes(
    1, bytes.fromhex("100218012001280030033800404148005204f488c824")
)
PREHEAT_REPORT_OFF = _field_bytes(
    1, bytes.fromhex("10021801280030033800404148005204b888d023")
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (PREHEAT_REPORT_ON, PreheatTask(1, 2, 65, enabled=True, start=1140, end=1170)),
        (
            PREHEAT_REPORT_OFF,
            PreheatTask(1, 2, 65, enabled=False, start=1080, end=1140),
        ),
    ],
    ids=["on 19:00-19:30", "off 18:00-19:00"],
)
def test_parse_preheat_report(content: bytes, expected: PreheatTask) -> None:
    assert parse_preheat_report(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        b"",  # never set up
        _field_varint(1, 5),
        # a task of another type (1)
        _field_bytes(1, bytes.fromhex("100218013001404148005204f488c824")),
        # end before start: 19:30-19:00
        _field_bytes(1, bytes.fromhex("1002180130034041480052049289d023")),
    ],
    ids=["empty", "no task", "other type", "end before start"],
)
def test_parse_preheat_report_without_a_task(content: bytes) -> None:
    assert parse_preheat_report(content) is None


def test_energy_stream_request_as_the_app_sends_it() -> None:
    message = energy_stream_request(SN, seq=1234)
    ((_, header),) = protobuf_fields(message)
    # Field by field as captured from the Android app (every 10 s)
    assert protobuf_fields(header) == [
        (1, bytes.fromhex("0801")),  # field 1 = 1 (on)
        (2, 32),
        (3, 96),
        (4, 1),
        (5, 1),
        (8, 96),
        (9, 97),
        (10, 2),
        (11, 1),
        (14, 1234),
        (15, 1),
        (16, 19),
        (17, 1),
        (23, b"android"),
        (25, SN.encode()),
    ]


# Contents of energy stream reports (96/33) as captured: at noon, and in
# the evening with an empty battery and no sun (zeros left out)
STREAM_NOON = bytes.fromhex(
    "0d0000dc4415000020411d00c08a4425000020c428283500003e443d0000aa43"
)
STREAM_EVENING = bytes.fromhex("0d0040544415004054442805")


def test_energy_stream_values() -> None:
    values = energy_stream(Part(96, 33, STREAM_NOON, None))

    assert values == {
        "sysLoadPwr": 1760.0,
        "sysGridPwr": 10.0,
        "mpptPwr": 1110.0,
        "bpPwr": -640.0,
        "pv1Pwr": 760.0,
        "pv2Pwr": 340.0,
        "bpSoc": 40,
    }
    # House = grid + solar - battery (battery positive = charging)
    assert values["sysLoadPwr"] == (
        values["sysGridPwr"] + values["mpptPwr"] - values["bpPwr"]
    )


def test_energy_stream_missing_power_is_zero() -> None:
    values = energy_stream(Part(96, 33, STREAM_EVENING, None))

    # No PV strings in the report: none in the values either
    assert values == {
        "sysLoadPwr": 849.0,
        "sysGridPwr": 849.0,
        "mpptPwr": 0.0,
        "bpPwr": 0.0,
        "bpSoc": 5,
    }


def test_energy_stream_only_from_its_report() -> None:
    assert energy_stream(Part(96, 8, STREAM_NOON, None)) == {}


@pytest.fixture
def fresh_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget which unknown fields were already logged."""
    monkeypatch.setattr(
        "custom_components.powerocean.mqtt._logged_unknown_fields", set()
    )


@pytest.mark.usefixtures("fresh_unknown_fields")
def test_energy_stream_logs_an_unknown_field_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # STREAM_NOON plus field 8 = 190.0 W (float), as a third PV string might be
    content = STREAM_NOON + bytes.fromhex("4500003e43")

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        values = energy_stream(Part(96, 33, content, None))
        energy_stream(Part(96, 33, content, None))

    # The known values are not affected
    assert values == energy_stream(Part(96, 33, STREAM_NOON, None))
    lines = [r for r in caplog.records if "unknown fields" in r.getMessage()]
    assert len(lines) == 1
    assert "[8]" in lines[0].getMessage()
    assert "8: 190.0" in lines[0].getMessage()
    assert content.hex() in lines[0].getMessage()


@pytest.mark.usefixtures("fresh_unknown_fields")
def test_energy_stream_without_unknown_fields_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        energy_stream(Part(96, 33, STREAM_NOON, None))

    assert "unknown fields" not in caplog.text


def _with_house_power(raw: str) -> bytes:
    """Return STREAM_NOON with field 1 (house power) as these 4 bytes."""
    return bytes.fromhex("0d" + raw) + STREAM_NOON[5:]


# 0x75e9b266 is the 5.9e32 W of issue #189; 0x7f800000 is infinity,
# 0x7fc00000 not a number
@pytest.mark.parametrize("raw", ["66b2e975", "0000807f", "0000c07f"])
def test_energy_stream_with_impossible_values_is_ignored(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    content = _with_house_power(raw)

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        assert energy_stream(Part(96, 33, content, None)) == {}

    # The content is logged, to find out what the system sent
    assert content.hex() in caplog.text


def test_energy_stream_up_to_one_megawatt_is_kept() -> None:
    # 1,000,000.0 W as float
    values = energy_stream(Part(96, 33, _with_house_power("00247449"), None))

    assert values["sysLoadPwr"] == 1_000_000.0


def test_energy_stream_with_a_varint_power_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Issue #199: field 1 as varint (-1 as 64-bit), no 32-bit float
    content = bytes.fromhex("08ffffffffffffffffff01") + STREAM_NOON[5:]

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        assert energy_stream(Part(96, 33, content, None)) == {}

    assert content.hex() in caplog.text


def test_energy_stream_without_power_values_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Issue #199: only the battery level (field 5), no power field at all.
    # Not 0 W everywhere: the house always uses some power
    content = bytes.fromhex("2805")

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        assert energy_stream(Part(96, 33, content, None)) == {}

    assert "without power values" in caplog.text


def _message(*fields: tuple[int, int | bytes]) -> bytes:
    header = b"".join(
        _field_bytes(number, value)
        if isinstance(value, bytes)
        else _field_varint(number, value)
        for number, value in fields
    )
    return _field_bytes(1, header)


# Answers of the system to the LED command, structure as captured: the
# acknowledgement on set_reply and the EMS change report with field 152
LED_ACK = _message((2, 96), (3, 32), (8, 96), (9, 119), (11, 1), (12, 1), (14, 1234))
LED_REPORT = _message(
    (1, _field_varint(152, 20)), (2, 96), (3, 32), (8, 96), (9, 8), (14, 99)
)
# Another EMS change report, without the LED brightness
OTHER_REPORT = _message((1, _field_varint(1, 5)), (2, 96), (8, 96), (9, 8))


def test_module_versions_from_get_reply() -> None:
    assert module_versions(GET_REPLY) == MODULES


@pytest.mark.parametrize(
    "payload",
    [
        b'{"params": {}}',  # JSON, not protobuf
        b"\x0a\x05\x08\x01",  # truncated
        get_all_request(),  # no module info
    ],
    ids=["json", "truncated", "get_all_request"],  # seq changes on every call
)
def test_module_versions_without_module_info(payload: bytes) -> None:
    assert module_versions(payload) is None


def test_parse_module_info_skips_incomplete_entries() -> None:
    def entry(*fields: tuple[int, int]) -> bytes:
        return _field_bytes(1, b"".join(_field_varint(*field) for field in fields))

    version_1_0_0_0 = 0x01000000
    content = (
        entry((6, 106))  # no firmware
        + entry((8, version_1_0_0_0))  # no address
        + entry((6, 96), (8, version_1_0_0_0))
    )
    assert parse_module_info(content) == {96: ModuleVersion(96, 0, "1.0.0.0")}


def _callbacks() -> MqttCallbacks:
    return MqttCallbacks(
        on_modules=MagicMock(),
        on_settings=MagicMock(),
        on_ack=MagicMock(),
        on_energy_stream=MagicMock(),
        on_preheat=MagicMock(),
    )


@pytest.fixture
def connection() -> tuple[PowerOceanMqtt, MqttCallbacks]:
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, _callbacks())
    mqtt.client = MagicMock()
    return mqtt, mqtt.callbacks


def test_connection_uses_the_app_credentials() -> None:
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, _callbacks())
    assert (mqtt.host, mqtt.port) == ("mqtt.example.com", 8883)
    assert mqtt.client._client_id.decode().startswith("ANDROID_")
    assert mqtt.client._client_id.decode().endswith(f"_{USER_ID}")
    assert mqtt.client._username == b"app-account"


def test_on_connect_subscribes_and_requests_full_state(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection
    reason = MagicMock(is_failure=False)

    mqtt._on_connect(mqtt.client, None, None, reason, None)

    topics = [call.args[0] for call in mqtt.client.subscribe.call_args_list]
    assert topics == [
        f"/app/device/property/{SN}",
        f"/app/{USER_ID}/{SN}/thing/property/get_reply",
        f"/app/{USER_ID}/{SN}/thing/property/set_reply",
    ]
    topic, payload = mqtt.client.publish.call_args.args
    assert topic == f"/app/{USER_ID}/{SN}/thing/property/get"
    assert _header(payload) == _header(get_all_request())


def test_on_connect_refused_sends_nothing(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt._on_connect(mqtt.client, None, None, MagicMock(is_failure=True), None)

    mqtt.client.subscribe.assert_not_called()
    mqtt.client.publish.assert_not_called()


DEVICE_TOPIC = f"/app/device/property/{SN}"
SET_REPLY_TOPIC = f"/app/{USER_ID}/{SN}/thing/property/set_reply"


def _receive(mqtt: PowerOceanMqtt, topic: str, payload: bytes) -> None:
    mqtt._on_message(mqtt.client, None, MagicMock(topic=topic, payload=payload))


def test_on_message_hands_over_module_info(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, DEVICE_TOPIC, b"not protobuf")
    callbacks.on_modules.assert_not_called()

    _receive(mqtt, f"/app/{USER_ID}/{SN}/thing/property/get_reply", GET_REPLY)
    callbacks.on_modules.assert_called_once_with(MODULES)


def test_set_led_brightness_publishes_the_command(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt.set_led_brightness(40, seq=1234)

    mqtt.client.publish.assert_called_once_with(
        f"/app/{USER_ID}/{SN}/thing/property/set",
        led_brightness_request(SN, 40, 1234),
        qos=1,
    )


def test_set_soc_limits_publishes_the_command(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt.set_soc_limits(100, 11, 100, seq=1234)

    mqtt.client.publish.assert_called_once_with(
        f"/app/{USER_ID}/{SN}/thing/property/set",
        soc_limits_request(SN, 100, 11, 100, 1234),
        qos=1,
    )


def test_on_message_hands_over_ack_and_led_brightness(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, SET_REPLY_TOPIC, LED_ACK)
    callbacks.on_ack.assert_called_once_with(1234)

    _receive(mqtt, DEVICE_TOPIC, OTHER_REPORT)
    callbacks.on_settings.assert_not_called()

    _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
    callbacks.on_settings.assert_called_once_with({"emsCtrlLedBright": 20})


def test_on_message_hands_over_ack_and_soc_limits(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    # Acknowledgement of the backup reserve, as captured (96/112)
    _receive(
        mqtt,
        SET_REPLY_TOPIC,
        _message((2, 96), (3, 32), (8, 96), (9, 112), (11, 1), (12, 1), (14, 77)),
    )
    callbacks.on_ack.assert_called_once_with(77)

    # Backup reserve (field 12) and charge limit (field 11) in a change report
    report = _field_varint(11, 90) + _field_varint(12, 11) + _field_varint(1, 5)
    _receive(mqtt, DEVICE_TOPIC, _message((1, report), (2, 96), (8, 96), (9, 8)))
    callbacks.on_settings.assert_called_once_with(
        {"sysBatChgUpLimit": 90, "sysBatDsgDownLimit": 11}
    )


def test_preheat_acknowledged_on_the_device_topic(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    # Answer to 96/201 as captured: on the device topic, not on set_reply
    _receive(
        mqtt,
        DEVICE_TOPIC,
        _message(
            (1, bytes.fromhex("08031001")),
            (2, 96),
            (3, 32),
            (8, 96),
            (9, 201),
            (14, 77),
        ),
    )

    callbacks.on_ack.assert_called_once_with(77)
    # The confirmation does not say what changed (maybe in the app): ask
    ((topic, payload), kwargs) = mqtt.client.publish.call_args
    assert topic == f"/app/{USER_ID}/{SN}/thing/property/set"
    ((_, header),) = protobuf_fields(payload)
    assert dict(protobuf_fields(header))[9] == 203
    assert kwargs == {"qos": 1}


def test_on_message_hands_over_the_preheating(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(
        mqtt,
        DEVICE_TOPIC,
        _message((1, PREHEAT_REPORT_ON), (2, 96), (8, 96), (9, 203), (14, 5)),
    )

    callbacks.on_preheat.assert_called_once_with(
        PreheatTask(1, 2, 65, enabled=True, start=1140, end=1170)
    )
    callbacks.on_ack.assert_not_called()


def test_set_preheat_publishes_the_command(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection
    task = PreheatTask(1, 2, 65, enabled=True, start=810, end=900)

    mqtt.set_preheat(task, seq=1234)

    mqtt.client.publish.assert_called_once_with(
        f"/app/{USER_ID}/{SN}/thing/property/set",
        preheat_request(SN, task, 1234),
        qos=1,
    )


def test_ack_only_from_set_reply(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    # The same message on the device topic is no acknowledgement
    _receive(mqtt, DEVICE_TOPIC, LED_ACK)

    callbacks.on_ack.assert_not_called()


def test_set_battery_save_mode_publishes_the_command(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt.set_battery_save_mode(1, 4, seq=1234)

    mqtt.client.publish.assert_called_once_with(
        f"/app/{USER_ID}/{SN}/thing/property/set",
        battery_save_mode_request(SN, 1, 4, 1234),
        qos=1,
    )


def test_on_message_hands_over_the_parameter_change_report(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    # As captured after the app switched the save mode off: field 2 = 0
    # (explicitly), field 3 = sysZone; field 10 = devSoc after another change
    report = _field_varint(2, 0) + _field_varint(3, 4) + _field_varint(10, 77)
    _receive(mqtt, DEVICE_TOPIC, _message((1, report), (2, 96), (8, 96), (9, 13)))

    callbacks.on_settings.assert_called_once_with(
        {"batterySaveMode": 0, "sysZone": 4, "devSoc": 77}
    )


def test_request_energy_stream_publishes_the_request(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt.request_energy_stream()

    topic, payload = mqtt.client.publish.call_args.args
    assert topic == f"/app/{USER_ID}/{SN}/thing/property/set"
    ((_, header),) = protobuf_fields(payload)
    fields = dict(protobuf_fields(header))
    assert (fields[8], fields[9], fields[1]) == (96, 97, bytes.fromhex("0801"))


def test_on_message_hands_over_the_energy_stream(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, DEVICE_TOPIC, _message((1, STREAM_NOON), (2, 96), (8, 96), (9, 33)))

    callbacks.on_energy_stream.assert_called_once_with(
        energy_stream(Part(96, 33, STREAM_NOON, None))
    )
    callbacks.on_settings.assert_not_called()


def test_on_message_survives_an_error(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Issue #199: an error must not end paho's thread (and the connection)
    mqtt, callbacks = connection
    callbacks.on_energy_stream.side_effect = [RuntimeError("boom"), None]
    payload = _message((1, STREAM_NOON), (2, 96), (8, 96), (9, 33))

    _receive(mqtt, DEVICE_TOPIC, payload)
    _receive(mqtt, DEVICE_TOPIC, payload)

    assert callbacks.on_energy_stream.call_count == 2
    assert "MQTT message could not be processed" in caplog.text


# Encrypted energy stream reports of a PowerOcean Plus from issue #189
# (enc_type 1), with the key (lowest byte of the sequence number) and the
# house power: in the evening, no sun, battery idle at 75 %
ENCRYPTED_STREAMS = [
    ("252868646c3d2868646c0063", 0x28, 817.0),
    ("1518d8305d0d18d8305d3053", 0x18, 2700.0),
    ("101d1da959081d1da9593556", 0x1D, 1440.0),
    ("2d2060db64352060db64086b", 0x20, 2010.0),
    ("3538f8847c2d38f8847c1073", 0x38, 1510.0),
    ("1d10d0a2540510d0a254385b", 0x10, 1430.0),
    ("2825259c613025259c610d6e", 0x25, 1480.0),
    ("0508486a4c1d08486a4c2043", 0x08, 905.0),
]


@pytest.mark.parametrize(("content", "key", "house"), ENCRYPTED_STREAMS)
def test_split_message_decrypts_an_encrypted_energy_stream(
    content: str, key: int, house: float
) -> None:
    seq = 0x5A3C_0000 | key  # only the lowest byte is the key
    payload = _message(
        (1, bytes.fromhex(content)), (2, 96), (6, 1), (8, 96), (9, 33), (14, seq)
    )

    [part] = split_message(payload)

    assert energy_stream(part) == {
        "sysLoadPwr": house,
        "sysGridPwr": house,
        "mpptPwr": 0.0,
        "bpPwr": 0.0,
        "bpSoc": 75,
    }
    assert part.seq == seq


def test_split_message_leaves_plain_content_alone() -> None:
    # Without enc_type the content is not encrypted, whatever the seq
    payload = _message((1, STREAM_NOON), (2, 96), (8, 96), (9, 33), (14, 0x28))

    [part] = split_message(payload)

    assert part.content == STREAM_NOON


def test_on_message_hands_over_an_encrypted_energy_stream(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection
    content, key, house = ENCRYPTED_STREAMS[0]

    _receive(
        mqtt,
        DEVICE_TOPIC,
        _message(
            (1, bytes.fromhex(content)), (2, 96), (6, 1), (8, 96), (9, 33), (14, key)
        ),
    )

    callbacks.on_energy_stream.assert_called_once()
    assert callbacks.on_energy_stream.call_args.args[0]["sysLoadPwr"] == house


def test_on_message_drops_an_energy_stream_with_impossible_values(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection
    content = _with_house_power("66b2e975")

    _receive(mqtt, DEVICE_TOPIC, _message((1, content), (2, 96), (8, 96), (9, 33)))

    callbacks.on_energy_stream.assert_not_called()


@pytest.fixture
def fresh_logged_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget which commands were already logged."""
    monkeypatch.setattr("custom_components.powerocean.mqtt._logged_commands", set())


def _debug_lines(caplog: pytest.LogCaptureFixture, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if text in r.getMessage()]


@pytest.mark.usefixtures("fresh_logged_commands")
def test_each_command_logged_once(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mqtt, _ = connection

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
        _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
        _receive(mqtt, SET_REPLY_TOPIC, LED_ACK)

    assert _debug_lines(caplog, "MQTT first message") == [
        "MQTT first message 96/8",
        "MQTT first message 96/119 (set_reply)",
    ]


@pytest.mark.usefixtures("fresh_logged_commands")
def test_settings_report_logged_with_its_fields(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mqtt, _ = connection
    # A number, and a nested message that may hold a serial number
    report = _field_varint(12, 75) + _field_bytes(5, b"SN_SECRET")
    message = _message((1, report), (2, 96), (8, 96), (9, 8))

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        _receive(mqtt, DEVICE_TOPIC, message)
        _receive(mqtt, DEVICE_TOPIC, message)

    # Every time, so a change in the app shows up
    assert (
        _debug_lines(caplog, "MQTT settings report")
        == ["MQTT settings report 96/8: {12: 75, 5: '<9 bytes>'}"] * 2
    )
    assert "SN_SECRET" not in caplog.text


@pytest.mark.usefixtures("fresh_logged_commands")
def test_other_reports_logged_without_fields(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mqtt, _ = connection

    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        _receive(mqtt, SET_REPLY_TOPIC, LED_ACK)
        _receive(mqtt, DEVICE_TOPIC, _message((1, STREAM_NOON), (8, 96), (9, 33)))

    assert _debug_lines(caplog, "MQTT settings report") == []


@pytest.mark.usefixtures("fresh_logged_commands", "fresh_unknown_fields")
def test_debug_logging_switched_on_later_still_logs_once(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Debug logging switched on in the UI while the integration runs
    mqtt, _ = connection
    unknown = STREAM_NOON + bytes.fromhex("4500003e43")

    with caplog.at_level("INFO", logger="custom_components.powerocean"):
        _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
        energy_stream(Part(96, 33, unknown, None))
    with caplog.at_level("DEBUG", logger="custom_components.powerocean"):
        _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
        energy_stream(Part(96, 33, unknown, None))

    assert _debug_lines(caplog, "MQTT first message") == ["MQTT first message 96/8"]
    assert len(_debug_lines(caplog, "unknown fields")) == 1
