"""
mqtt.py: MQTT connection to the EcoFlow cloud, as the EcoFlow app uses it.

Optional next to the HTTP polling: the integration connects with the MQTT
credentials of the account, asks the system for its full state (as the app
does when it opens) and reads the firmware of its modules from the answer.
The only settings it sends are the brightness of the LED and the backup
reserve, as the app does.

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
EMS = 96
EMS_CHANGE_REPORT = 8
LED_BRIGHTNESS = 119
# Charge limit, backup reserve and devSoc in one command
SOC_LIMITS = 112
# Settings in the EMS change report: field number -> datapoint
CHANGE_REPORT_SETTINGS = {
    11: "sysBatChgUpLimit",
    # The backup reserve of the app
    12: "sysBatDsgDownLimit",
    152: "emsCtrlLedBright",
}
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


def new_seq() -> int:
    """Return a sequence number as the app makes it (ms timestamp)."""
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
            _field_varint(14, new_seq()),  # seq
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
            _field_varint(14, new_seq()),  # seq
            _field_varint(15, 1),  # product_id
            _field_varint(16, 19),  # version
            _field_varint(17, 1),  # payload_ver
            _field_bytes(23, b"android"),  # from
            _field_bytes(25, serial.encode()),  # device_sn
        )
    )
    return _field_bytes(1, header)


def led_brightness_request(serial: str, percent: int, seq: int) -> bytes:
    """
    Return the command that sets the LED brightness (96/119), as the app sends it.

    Content: field 1 = 1 (probably the LED type), field 2 = brightness in %.
    The system acknowledges it on .../set_reply with the same seq and reports
    the new value in an EMS change report (96/8, field 152).
    """
    content = _field_varint(1, 1) + _field_varint(2, percent)
    return _ems_command(serial, LED_BRIGHTNESS, content, seq)


def soc_limits_request(
    serial: str, charge_limit: int, backup_reserve: int, dev_soc: int, seq: int
) -> bytes:
    """
    Return the command that sets the backup reserve (96/112), as the app sends it.

    Content: field 1 = charge limit, field 2 = backup reserve, field 4 = devSoc,
    all in %. The app sends the current charge limit and devSoc along, so
    they must be known. The system acknowledges it on .../set_reply and
    reports the new value in an EMS change report (96/8, field 12).
    """
    content = (
        _field_varint(1, charge_limit)
        + _field_varint(2, backup_reserve)
        + _field_varint(4, dev_soc)
    )
    return _ems_command(serial, SOC_LIMITS, content, seq)


def _ems_command(serial: str, cmd_id: int, content: bytes, seq: int) -> bytes:
    """Return a setting command of the app to the EMS."""
    header = b"".join(
        (
            _field_bytes(1, content),  # pdata
            _field_varint(2, APP),  # src
            _field_varint(3, EMS),  # dest
            _field_varint(4, 1),  # d_src
            _field_varint(5, 1),  # d_dest
            _field_varint(8, EMS),  # cmd_func
            _field_varint(9, cmd_id),  # cmd_id
            _field_varint(10, len(content)),  # data_len
            _field_varint(11, 1),  # need_ack
            _field_varint(14, seq),  # seq
            _field_varint(15, 1),  # product_id
            _field_varint(16, 19),  # version
            _field_varint(17, 1),  # payload_ver
            _field_bytes(23, b"android"),  # from
            _field_bytes(25, serial.encode()),  # device_sn
        )
    )
    return _field_bytes(1, header)


@dataclass(frozen=True)
class Part:
    """One part of a message: command set and id, content, sequence number."""

    cmd_func: int | None
    cmd_id: int | None
    content: bytes
    seq: int | None


def _int(value: int | bytes | None) -> int | None:
    return value if isinstance(value, int) else None


def split_message(payload: bytes) -> list[Part]:
    """Return the parts of a message."""
    parts: list[Part] = []
    for number, header in protobuf_fields(payload):
        if number != 1 or not isinstance(header, bytes):
            continue
        fields = dict(protobuf_fields(header))
        content = fields.get(1, b"")
        parts.append(
            Part(
                cmd_func=_int(fields.get(8)),
                cmd_id=_int(fields.get(9)),
                content=content if isinstance(content, bytes) else b"",
                seq=_int(fields.get(14)),
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
        for part in split_message(payload):
            if (part.cmd_func, part.cmd_id) == (IOT, MODULE_INFO) and part.content:
                return parse_module_info(part.content)
    except ValueError:
        LOGGER.debug("MQTT message is no protobuf message")
    return None


def change_report_settings(part: Part) -> dict[str, int]:
    """Return the settings of an EMS change report by datapoint, {} without."""
    if (part.cmd_func, part.cmd_id) != (EMS, EMS_CHANGE_REPORT):
        return {}
    return {
        CHANGE_REPORT_SETTINGS[number]: value
        for number, value in protobuf_fields(part.content)
        if number in CHANGE_REPORT_SETTINGS and isinstance(value, int)
    }


# -----------------------------
# Connection
# -----------------------------


@dataclass(frozen=True)
class MqttCallbacks:
    """What the connection reports, called from paho's thread."""

    # Module info: firmware of the modules, by address
    on_modules: Callable[[dict[int, ModuleVersion]], None]
    # Settings changed (EMS change report), by datapoint
    on_settings: Callable[[dict[str, int]], None]
    # The system acknowledged a command, by its seq
    on_ack: Callable[[int], None]


class PowerOceanMqtt:
    """
    MQTT connection of one system: module versions and settings.

    paho runs its own thread: `start`, `stop`, `request_full_state` and the
    `set_...` commands block and belong in an executor; the callbacks are
    called from that thread.
    """

    def __init__(
        self,
        credentials: dict[str, Any],
        serial: str,
        user_id: str,
        callbacks: MqttCallbacks,
    ) -> None:
        """Prepare the connection with the MQTT credentials of the account."""
        self.serial = serial
        self.user_id = user_id
        self.callbacks = callbacks
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
            f"{self._property_topic}/set_reply",
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

    def set_led_brightness(self, percent: int, seq: int) -> None:
        """Send the LED brightness in % (acknowledged with `seq`)."""
        self.client.publish(
            f"{self._property_topic}/set",
            led_brightness_request(self.serial, percent, seq),
            qos=1,
        )

    def set_soc_limits(
        self, charge_limit: int, backup_reserve: int, dev_soc: int, seq: int
    ) -> None:
        """Send charge limit, backup reserve and devSoc in % (ack with `seq`)."""
        self.client.publish(
            f"{self._property_topic}/set",
            soc_limits_request(self.serial, charge_limit, backup_reserve, dev_soc, seq),
            qos=1,
        )

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
        reply = message.topic.endswith("/set_reply")
        try:
            for part in split_message(message.payload):
                command = (part.cmd_func, part.cmd_id)
                if command == (IOT, MODULE_INFO) and part.content:
                    self.callbacks.on_modules(parse_module_info(part.content))
                elif reply:
                    # By seq: only the integration's own commands wait for it
                    if part.seq is not None:
                        self.callbacks.on_ack(part.seq)
                elif settings := change_report_settings(part):
                    self.callbacks.on_settings(settings)
        except ValueError:
            LOGGER.debug("MQTT message is no protobuf message")
