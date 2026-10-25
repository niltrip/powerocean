"""
mqtt.py: MQTT connection to the EcoFlow cloud, as the EcoFlow app uses it.

Optional next to the HTTP polling: the integration connects with the MQTT
credentials of the account, asks the system for its full state (as the app
does when it opens) and reads the firmware of its modules from the answer.
Nothing else is sent.

The messages are protobuf. This module encodes and splits them without
.proto files; the layout was captured from the Android app and matches
shuette42/ecoflow-energy-ha (MIT).

This module does not depend on Home Assistant.
"""

from __future__ import annotations

import ssl
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

from .const import LOGGER
from .parse import decode_version

if TYPE_CHECKING:
    from collections.abc import Callable

# Protobuf wire types
VARINT, FIXED64, LENGTH, FIXED32 = 0, 1, 2, 5

# Command sets and ids (cmd_func / cmd_id)
IOT = 53
MODULE_INFO = 113
# Source of the requests: the app
APP = 32

# Address of the modules in the module info, as the system lists them
MODULE_EMS = 96
MODULE_METER = 106


# -----------------------------
# Protobuf without .proto files
# -----------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if not value:
            out.append(byte)
            return bytes(out)
        out.append(byte | 0x80)


def _field_varint(number: int, value: int) -> bytes:
    return _varint(number << 3 | VARINT) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | LENGTH) + _varint(len(value)) + value


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            msg = "truncated varint"
            raise ValueError(msg)
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def protobuf_fields(data: bytes) -> list[tuple[int, int | bytes]]:
    """Return (field number, value) of a protobuf message; raises on garbage."""
    fields: list[tuple[int, int | bytes]] = []
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire = key >> 3, key & 7
        value: int | bytes
        if wire == VARINT:
            value, pos = _read_varint(data, pos)
        elif wire == LENGTH:
            length, pos = _read_varint(data, pos)
            value, pos = data[pos : pos + length], pos + length
        elif wire == FIXED32:
            value, pos = int.from_bytes(data[pos : pos + 4], "little"), pos + 4
        elif wire == FIXED64:
            value, pos = int.from_bytes(data[pos : pos + 8], "little"), pos + 8
        else:
            msg = f"unknown wire type {wire}"
            raise ValueError(msg)
        if pos > len(data):
            msg = "truncated field"
            raise ValueError(msg)
        fields.append((number, value))
    return fields


def _seq() -> int:
    return int(time.time() * 1000) & 0x7FFFFFFF


# -----------------------------
# Messages
# -----------------------------


def get_all_request() -> bytes:
    """
    Return the request for the full state, as the app sends it.

    Header only; the system answers with all reports on .../get_reply.
    """
    header = b"".join(
        (
            _field_varint(2, APP),  # src
            _field_varint(3, APP),  # dest
            _field_varint(14, _seq()),  # seq
            _field_bytes(23, b"Android"),  # from
            _field_bytes(26, b""),
            _field_bytes(27, b""),
        )
    )
    return _field_bytes(1, header)


def module_info_request(serial: str) -> bytes:
    """Return the request for the module versions (53/113), as the app sends it."""
    header = b"".join(
        (
            _field_varint(2, APP),  # src
            _field_varint(3, IOT),  # dest
            _field_varint(4, 1),  # d_src
            _field_varint(5, 1),  # d_dest
            _field_varint(8, IOT),  # cmd_func
            _field_varint(9, MODULE_INFO),  # cmd_id
            _field_varint(11, 1),  # need_ack
            _field_varint(14, _seq()),  # seq
            _field_varint(15, 1),  # product_id
            _field_varint(16, 19),  # version
            _field_varint(17, 1),  # payload_ver
            _field_bytes(23, b"android"),  # from
            _field_bytes(25, serial.encode()),  # device_sn
        )
    )
    return _field_bytes(1, header)


def split_message(payload: bytes) -> list[tuple[int | None, int | None, bytes]]:
    """Return command set, command id and content of each part of a message."""
    parts: list[tuple[int | None, int | None, bytes]] = []
    for number, header in protobuf_fields(payload):
        if number != 1 or not isinstance(header, bytes):
            continue
        fields = dict(protobuf_fields(header))
        content = fields.get(1, b"")
        cmd_func, cmd_id = fields.get(8), fields.get(9)
        parts.append(
            (
                cmd_func if isinstance(cmd_func, int) else None,
                cmd_id if isinstance(cmd_id, int) else None,
                content if isinstance(content, bytes) else b"",
            )
        )
    return parts


@dataclass(frozen=True)
class ModuleVersion:
    """Firmware of one module of the system, from the module info."""

    address: int
    module_type: int
    firmware: str
    loader: str | None = None


def parse_module_info(content: bytes) -> dict[int, ModuleVersion]:
    """
    Return the modules of a module info (53/113), by address.

    Each module is a field 1 with: 2 module type, 6 address, 8 firmware and
    9 loader, both as 32-bit major.minor.patch.build (serial numbers in 3
    and 10 are not needed).
    """
    modules: dict[int, ModuleVersion] = {}
    for number, raw in protobuf_fields(content):
        if number != 1 or not isinstance(raw, bytes):
            continue
        fields = dict(protobuf_fields(raw))
        address, firmware = fields.get(6), fields.get(8)
        if not isinstance(address, int) or not isinstance(firmware, int):
            continue
        loader = fields.get(9)
        module_type = fields.get(2)
        modules[address] = ModuleVersion(
            address=address,
            module_type=module_type if isinstance(module_type, int) else 0,
            firmware=decode_version(firmware),
            loader=decode_version(loader) if isinstance(loader, int) else None,
        )
    return modules


def module_versions(payload: bytes) -> dict[int, ModuleVersion] | None:
    """Return the modules of the module info in a message, None without one."""
    try:
        parts = split_message(payload)
        for cmd_func, cmd_id, content in parts:
            if (cmd_func, cmd_id) == (IOT, MODULE_INFO) and content:
                return parse_module_info(content)
    except ValueError:
        LOGGER.debug("MQTT message is no protobuf message")
    return None


# -----------------------------
# Connection
# -----------------------------


class PowerOceanMqtt:
    """
    MQTT connection of one system, reading its module versions.

    paho runs its own thread: `start`, `stop` and `request_full_state` block
    and belong in an executor; `on_modules` is called from that thread.
    """

    def __init__(
        self,
        credentials: dict[str, Any],
        serial: str,
        user_id: str,
        on_modules: Callable[[dict[int, ModuleVersion]], None],
    ) -> None:
        """Prepare the connection with the MQTT credentials of the account."""
        self.serial = serial
        self.user_id = user_id
        self.on_modules = on_modules
        self.host = str(credentials["url"])
        self.port = int(credentials["port"])
        # Client id in the format of the Android app
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ANDROID_{uuid.uuid4().hex.upper()}_{user_id}",
        )
        self.client.username_pw_set(
            credentials["certificateAccount"], credentials["certificatePassword"]
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=5, max_delay=300)

    @property
    def _property_topic(self) -> str:
        return f"/app/{self.user_id}/{self.serial}/thing/property"

    def topics(self) -> list[str]:
        """Return the topics the system answers on."""
        return [
            f"/app/device/property/{self.serial}",
            f"{self._property_topic}/get_reply",
        ]

    def start(self) -> None:
        """
        Connect in paho's thread (blocking: loads the CA certificates).

        paho keeps trying, also when the cloud cannot be reached at the start.
        """
        self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        """Disconnect (blocking)."""
        self.client.disconnect()
        self.client.loop_stop()

    def request_full_state(self) -> None:
        """Ask the system for its full state, which holds the module info."""
        self.client.publish(f"{self._property_topic}/get", get_all_request(), qos=1)

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        reason: mqtt.ReasonCode,
        _props: Any,
    ) -> None:
        if reason.is_failure:
            LOGGER.warning("MQTT connection refused: %s", reason)
            return
        LOGGER.debug("MQTT connected")
        for topic in self.topics():
            client.subscribe(topic, qos=1)
        # Also after a reconnect: the system may have restarted (update)
        self.request_full_state()

    def _on_message(
        self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        modules = module_versions(message.payload)
        if modules:
            self.on_modules(modules)
