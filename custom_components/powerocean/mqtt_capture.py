"""
mqtt_capture.py: Mask recorded MQTT messages, to share them in an issue.

Used by the diagnostics (the messages of the last minutes, issue #198) and
by `scripts/capture.py`. A recording shows how a model reports and which
commands the app sends, so its protocol can be decoded.

Every text field is masked with a mask of the same length, so the message
keeps its structure: serial numbers get a numbered mask (the inverter keeps
its first characters: the model, not the device), other text only X. The
content of a part may be XOR-encrypted (issue #189): it is decrypted, masked
and encrypted again with the same key. A last check looks for every known
secret in the result, also in the decrypted contents.
"""

from __future__ import annotations

import re

from .mqtt import XOR_ENCRYPTED, decrypt, protobuf_fields

SERIAL_LENGTH = 16
# The inverter serial keeps its first characters: the model, not the device
KEEP_PREFIX = 4
# A serial in a message that is no protobuf (JSON): 16 letters and digits
SERIAL_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9])(?=[A-Za-z]*[0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])"
)
# Shorter text is kept: it would also match bytes of numbers by chance
MIN_TEXT_LENGTH = 6
# Text that says nothing about the user (header field "from" of the app)
PUBLIC_TEXT = {b"android", b"ios"}
FIRST_PRINTABLE, LAST_PRINTABLE = 32, 126
# Header fields of a message part
CONTENT, ENC_TYPE, CMD_FUNC, CMD_ID, SRC, DEST, SEQ = 1, 6, 8, 9, 2, 3, 14


def printable(value: bytes) -> bool:
    """Return whether a field is text."""
    return bool(value) and all(FIRST_PRINTABLE <= c <= LAST_PRINTABLE for c in value)


def _fields(data: bytes) -> list[tuple[int, int | bytes]]:
    try:
        return protobuf_fields(data)
    except (ValueError, IndexError):
        return []


def text_fields(data: bytes) -> list[bytes]:
    """Return all text fields of a protobuf message, also nested ones."""
    found: list[bytes] = []
    for _, value in _fields(data):
        if not isinstance(value, bytes):
            continue
        if printable(value):
            found.append(value)
        else:
            found.extend(text_fields(value))
    return found


def is_protobuf(data: bytes) -> bool:
    """Return whether the message parses as protobuf."""
    return bool(_fields(data))


def serials_in(payload: bytes) -> list[bytes]:
    """Return what looks like a serial number in a message."""
    if is_protobuf(payload):
        return [
            value
            for value in text_fields(payload)
            if len(value) == SERIAL_LENGTH and value.isalnum()
        ]
    return SERIAL_PATTERN.findall(payload)


def _headers(payload: bytes) -> list[dict[int, int | bytes]]:
    return [
        dict(_fields(header))
        for number, header in _fields(payload)
        if number == 1 and isinstance(header, bytes)
    ]


def summary(payload: bytes) -> list[str]:
    """Return cmd_func/cmd_id src->dest of each part of a message."""
    if not is_protobuf(payload):
        return ["no protobuf"]
    return [
        "{}/{} {}->{}".format(
            *(fields.get(n, "-") for n in (CMD_FUNC, CMD_ID, SRC, DEST))
        )
        for fields in _headers(payload)
    ]


def _encrypted_contents(payload: bytes) -> list[tuple[bytes, int]]:
    """Return the encrypted contents of a message with their seq (the key)."""
    found: list[tuple[bytes, int]] = []
    for fields in _headers(payload):
        content, seq = fields.get(CONTENT), fields.get(SEQ)
        if (
            fields.get(ENC_TYPE) == XOR_ENCRYPTED
            and isinstance(content, bytes)
            and content
            and isinstance(seq, int)
        ):
            found.append((content, seq))
    return found


class Masker:
    """Replace serial numbers, text and account data with masks of the same length."""

    def __init__(self, serial: str, user_id: str, private: list[str]) -> None:
        """Start with the inverter serial, the user id and other private text."""
        self.serial = serial.encode()
        self.user_id = user_id
        self.masks: dict[bytes, bytes] = {
            self.serial: self.serial[:KEEP_PREFIX]
            + b"X" * (len(self.serial) - KEEP_PREFIX),
        }
        for text in (user_id, *private):
            if text:
                self.masks.setdefault(text.encode(), b"X" * len(text.encode()))
        # Other text is only searched as bytes: as written text it would also
        # be found by chance in the hex of a message
        self.other_text: set[bytes] = set()
        self.count = 0

    def _learn(self, data: bytes) -> None:
        """Add a mask for every serial and every other text in `data`."""
        for found in serials_in(data):
            if found not in self.masks:
                self.count += 1
                number = str(self.count).encode()
                self.masks[found] = b"X" * (SERIAL_LENGTH - len(number)) + number
        for text in text_fields(data):
            if (
                len(text) >= MIN_TEXT_LENGTH
                and text.lower() not in PUBLIC_TEXT
                and text not in self.masks
            ):
                self.masks[text] = b"X" * len(text)
                self.other_text.add(text)

    def _replace(self, data: bytes) -> bytes:
        # Longest first: a serial may contain a shorter private text
        for secret in sorted(self.masks, key=len, reverse=True):
            data = data.replace(secret, self.masks[secret])
        return data

    def payload(self, data: bytes) -> bytes:
        """Return the message with every known and new serial and text masked."""
        for content, seq in _encrypted_contents(data):
            plain = decrypt(content, XOR_ENCRYPTED, seq)
            self._learn(plain)
            masked = self._replace(plain)
            if masked != plain:
                # XOR keeps the length: the message keeps its structure
                data = data.replace(content, decrypt(masked, XOR_ENCRYPTED, seq))
        self._learn(data)
        return self._replace(data)

    def topic(self, topic: str) -> str:
        """Return the topic without serial and user id."""
        return topic.replace(self.serial.decode(), "SN").replace(self.user_id, "USER")

    def leaks(self, text: str) -> bool:
        """Return whether the written text contains a secret in any form."""
        text = text.lower()
        for secret in self.masks:
            if secret in self.other_text:
                if secret.hex() in text:
                    return True
                continue
            forms = [secret]
            if secret == self.serial:
                forms.append(secret[KEEP_PREFIX:])
            for form in forms:
                if form.decode(errors="replace").lower() in text or form.hex() in text:
                    return True
        return False

    def leaks_in(self, payload: bytes) -> bool:
        """Return whether a masked message still holds a secret, also encrypted."""
        texts = [payload.hex(), payload.decode(errors="replace")]
        for content, seq in _encrypted_contents(payload):
            plain = decrypt(content, XOR_ENCRYPTED, seq)
            texts += [plain.hex(), plain.decode(errors="replace")]
        return any(self.leaks(text) for text in texts)
