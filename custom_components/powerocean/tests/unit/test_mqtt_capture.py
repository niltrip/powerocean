"""test_mqtt_capture: masking of recorded MQTT messages (issue #198)."""

import json

import pytest

from custom_components.powerocean.mqtt import (
    _field_bytes,
    _field_varint,
    decrypt,
    protobuf_fields,
    split_message,
)
from custom_components.powerocean.mqtt_capture import Masker, summary

# Made up, in the format of real ones: 16 letters and digits
SERIAL = "HJ31ZZTESTSN0001"
MODULE_SERIAL = "HJ37ZZTESTSN0002"
USER_ID = "1234567890123"
WIFI = "TestHomeWifi"
EMAIL = "someone@example.com"


def _part(content: bytes, *, seq: int = 0x1234, encrypted: bool = False) -> bytes:
    """Return a message with one part, as the system sends it."""
    if encrypted:
        content = decrypt(content, 1, seq)  # XOR: the same both ways
    header = b"".join(
        (
            _field_bytes(1, content),
            _field_varint(2, 53),
            _field_varint(3, 32),
            _field_varint(6, 1 if encrypted else 0),
            _field_varint(8, 53),
            _field_varint(9, 113),
            _field_varint(14, seq),
            _field_bytes(23, b"android"),
            _field_bytes(25, SERIAL.encode()),
        )
    )
    return _field_bytes(1, header)


# A module list: the module serial nested, a Wi-Fi name, a short text
CONTENT = _field_bytes(
    1,
    _field_varint(2, 4)
    + _field_bytes(3, MODULE_SERIAL.encode())
    + _field_bytes(7, WIFI.encode())
    + _field_bytes(9, b"v1"),
)


def _masker() -> Masker:
    return Masker(SERIAL, USER_ID, [EMAIL])


def _contents(payload: bytes) -> bytes:
    return b"".join(part.content for part in split_message(payload))


@pytest.mark.parametrize("encrypted", [False, True])
def test_serials_and_text_are_masked(*, encrypted: bool) -> None:
    payload = _part(CONTENT, encrypted=encrypted)

    masked = _masker().payload(payload)

    # Same length and structure: the protocol can still be decoded
    assert len(masked) == len(payload)
    assert summary(masked) == summary(payload) == ["53/113 53->32"]
    content = _contents(masked)
    for secret in (SERIAL, MODULE_SERIAL, WIFI):
        assert secret.encode() not in content
        assert secret.encode() not in masked
    # The inverter keeps its model, the module gets a number
    assert b"HJ31XXXXXXXXXXXX" in masked
    assert b"XXXXXXXXXXXXXXX1" in content
    assert b"X" * len(WIFI) in content
    # Short and public text stays
    assert b"v1" in content
    assert b"android" in masked


def test_encrypted_content_stays_encrypted() -> None:
    payload = _part(CONTENT, encrypted=True)

    masked = _masker().payload(payload)

    # The content is still XOR-encrypted with the key of the seq
    [header] = [v for n, v in protobuf_fields(masked) if n == 1]
    assert dict(protobuf_fields(header))[1] != _contents(masked)


def test_the_same_serial_gets_the_same_mask() -> None:
    masker = _masker()

    first = masker.payload(_part(CONTENT))
    second = masker.payload(_part(CONTENT, encrypted=True, seq=0x99))

    assert _contents(first) == _contents(second)


def test_user_id_and_private_text_in_a_json_message() -> None:
    payload = json.dumps({"sn": MODULE_SERIAL, "user": USER_ID, "mail": EMAIL}).encode()

    masked = _masker().payload(payload)

    for secret in (MODULE_SERIAL, USER_ID, EMAIL):
        assert secret.encode() not in masked
    assert summary(masked) == ["no protobuf"]


def test_topic_without_serial_and_user_id() -> None:
    topic = f"/app/{USER_ID}/{SERIAL}/thing/property/set"

    assert _masker().topic(topic) == "/app/USER/SN/thing/property/set"


@pytest.mark.parametrize("encrypted", [False, True])
def test_leak_check_finds_a_secret_also_encrypted(*, encrypted: bool) -> None:
    masker = _masker()
    payload = _part(CONTENT, encrypted=encrypted)
    masker.payload(payload)  # learns the module serial and the Wi-Fi name

    # Unmasked: found, also when only the encrypted content holds it
    assert masker.leaks_in(payload)
    assert not masker.leaks_in(masker.payload(payload))


def test_leak_check_ignores_text_that_looks_like_hex() -> None:
    masker = _masker()
    # "abcdef" would be found by chance in the hex of a message
    masker.payload(_part(_field_bytes(7, b"abcdef")))

    # Bytes ab cd ef, no text: their hex reads "abcdef"
    masked = masker.payload(_part(_field_bytes(5, b"\xab\xcd\xef\x01")))

    assert "abcdef" in masked.hex()
    assert not masker.leaks_in(masked)


# The vehicle id of a wallbox is short text (issue #199): only masked as a
# known private text, from the HTTP response
VEHICLE_ID = "Ab3Z"
WALLBOX_CONTENT = (
    _field_bytes(1, MODULE_SERIAL.encode())
    + _field_varint(4, 2)
    + _field_bytes(13, VEHICLE_ID.encode())
    + _field_bytes(14, VEHICLE_ID.encode())
)


@pytest.mark.parametrize("encrypted", [False, True])
def test_short_private_text_is_masked(*, encrypted: bool) -> None:
    payload = _part(WALLBOX_CONTENT, encrypted=encrypted)

    masked = Masker(SERIAL, USER_ID, [EMAIL, VEHICLE_ID]).payload(payload)

    content = _contents(masked)
    assert VEHICLE_ID.encode() not in content
    assert content.count(b"XXXX") >= 2
    assert summary(masked) == summary(payload)


def test_short_text_is_found_by_the_leak_check() -> None:
    masker = Masker(SERIAL, USER_ID, [EMAIL, VEHICLE_ID])

    assert masker.leaks_in(_part(WALLBOX_CONTENT, encrypted=True))
    assert not masker.leaks_in(masker.payload(_part(WALLBOX_CONTENT, encrypted=True)))
