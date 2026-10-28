"""test_mqtt: protobuf messages and module info of the MQTT connection."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.powerocean.mqtt import (
    ModuleVersion,
    MqttCallbacks,
    PowerOceanMqtt,
    _field_bytes,
    _field_varint,
    get_all_request,
    led_brightness_request,
    module_info_request,
    module_versions,
    parse_module_info,
    protobuf_fields,
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
        on_modules=MagicMock(), on_led_brightness=MagicMock(), on_ack=MagicMock()
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


def test_on_message_hands_over_ack_and_led_brightness(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, SET_REPLY_TOPIC, LED_ACK)
    callbacks.on_ack.assert_called_once_with(1234)

    _receive(mqtt, DEVICE_TOPIC, OTHER_REPORT)
    callbacks.on_led_brightness.assert_not_called()

    _receive(mqtt, DEVICE_TOPIC, LED_REPORT)
    callbacks.on_led_brightness.assert_called_once_with(20)


def test_ack_only_from_set_reply(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    # The same message on the device topic is no acknowledgement
    _receive(mqtt, DEVICE_TOPIC, LED_ACK)

    callbacks.on_ack.assert_not_called()
