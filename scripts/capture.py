# ruff: noqa: INP001, T201 (a script, not a package; prints what it receives)
"""
Record the MQTT messages of a PowerOcean, to decode the protocol.

Connects with the account like the EcoFlow app and only listens: it sends
nothing to the system. Open the app while it runs and use the function you
want to decode; the recording contains the app's commands (topic .../set)
and the answers of the system. Type a note and press Enter to mark what you
just did in the app (for example "LED 40 %"). "q" and Enter or Ctrl+C ends.

    scripts/capture

Credentials are asked for and not stored. Instead of asking, the script
takes ECOFLOW_SN, ECOFLOW_EMAIL, ECOFLOW_PASSWORD and ECOFLOW_VARIANT from
~/.config/powerocean/developer.env (one NAME=value per line, outside the
repository) or from the environment. Every message is masked before it
is written: serial numbers keep their length (the inverter keeps its first
four characters), user id and account are removed from the topics. The file
in measurements/ (not committed) can be attached to an issue or discussion.

One JSON object per line: n, time_utc, t_rel_s and either topic, cmds
(cmd_func/cmd_id src->dest of each part) and hex (complete message), or
note (what you typed).
"""

import asyncio
import getpass
import json
import os
import ssl
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.powerocean.anonymize import redacted_values  # noqa: E402
from custom_components.powerocean.api import EcoflowApi, EcoflowApiError  # noqa: E402
from custom_components.powerocean.devices import MODEL_NAME_MAP  # noqa: E402
from custom_components.powerocean.mqtt_capture import Masker, summary  # noqa: E402

OUT_DIR = ROOT / "measurements"
CREDENTIALS_FILE = Path.home() / ".config/powerocean/developer.env"
# Input that ends the recording
STOP = ("q", ":q", "quit", "exit", "ende")


class Recorder:
    """Write masked messages and notes, one JSON object per line."""

    def __init__(self, out: Path, masker: Masker) -> None:
        """Open the output file."""
        self.file = out.open("w", encoding="utf-8")
        self.masker = masker
        self.start = time.monotonic()
        self.count = 0
        self.lock = threading.Lock()

    def _write(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.count += 1
            line = {
                "n": self.count,
                "time_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4]
                + "Z",
                "t_rel_s": round(time.monotonic() - self.start, 2),
                **record,
            }
            self.file.write(json.dumps(line) + "\n")
            self.file.flush()

    def message(self, topic: str, payload: bytes) -> None:
        """Mask and write one message, show it on the console."""
        masked = self.masker.payload(payload)
        record = {"topic": self.masker.topic(topic), "cmds": summary(masked)}
        if self.masker.leaks_in(masked):
            record["left_out"] = "masking incomplete"
        else:
            record["hex"] = masked.hex()
        self._write(record)
        # Only the app's commands and the answers to them: the reports of the
        # system come every second and would run through the note being typed
        kind = record["topic"].rsplit("/", 1)[-1]
        if kind in ("set", "set_reply", "get", "get_reply"):
            clock = datetime.now().astimezone().strftime("%H:%M:%S")
            print(f"{clock}  {kind:<10} {record['cmds']}")

    def note(self, text: str) -> None:
        """Write a note of the user."""
        self._write({"note": text})

    def close(self) -> None:
        """Close the output file."""
        self.file.close()


def connect(
    credentials: dict[str, Any], serial: str, user_id: str, recorder: Recorder
) -> mqtt.Client:
    """Connect like the app and subscribe; never publish."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"ANDROID_{uuid.uuid4().hex.upper()}_{user_id}",
    )
    client.username_pw_set(
        credentials["certificateAccount"], credentials["certificatePassword"]
    )
    property_topic = f"/app/{user_id}/{serial}/thing/property"
    topics = [
        f"/app/device/property/{serial}",
        f"{property_topic}/set",
        f"{property_topic}/set_reply",
        f"{property_topic}/get",
        f"{property_topic}/get_reply",
    ]

    def on_connect(
        client: mqtt.Client, _data: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        if reason.is_failure:
            print(f"Verbindung abgelehnt: {reason}")
            return
        for topic in topics:
            client.subscribe(topic, qos=1)
        print("Verbunden. Jetzt die App öffnen und bedienen.")
        recorder.note("(connected)")

    def on_disconnect(
        _client: mqtt.Client, _data: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        print(f"Verbindung getrennt ({reason}), verbinde neu …")
        recorder.note("(disconnected)")

    def on_message(_client: mqtt.Client, _data: Any, message: Any) -> None:
        recorder.message(message.topic, message.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=5, max_delay=60)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.connect_async(str(credentials["url"]), int(credentials["port"]), 60)
    client.loop_start()
    return client


async def account(api: EcoflowApi) -> tuple[str, dict[str, Any], list[str]]:
    """
    Log in and return user id, MQTT credentials and personal text to mask.

    The personal text (e.g. the vehicle id of a wallbox, issue #199) comes
    from the HTTP response.
    """
    try:
        await api.async_authorize()
        credentials = await api.async_mqtt_credentials()
        private = redacted_values(await api.fetch_raw())
    finally:
        await api.close()
    if not api.user_id:
        msg = "Die Anmeldung lieferte keine User-ID."
        raise EcoflowApiError(msg)
    return api.user_id, credentials, private


def saved_credentials() -> dict[str, str]:
    """Return the credentials of the file and the environment (environment wins)."""
    names = ("ECOFLOW_SN", "ECOFLOW_EMAIL", "ECOFLOW_PASSWORD", "ECOFLOW_VARIANT")
    saved: dict[str, str] = {}
    if CREDENTIALS_FILE.is_file():
        if CREDENTIALS_FILE.stat().st_mode & 0o077:
            print(f"Hinweis: chmod 600 {CREDENTIALS_FILE} (andere können sie lesen)")
        for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.strip().partition("=")
            if name.strip() in names and value.strip():
                saved[name.strip()] = value.strip().strip("\"'")
    saved.update({name: os.environ[name] for name in names if os.environ.get(name)})
    if saved:
        print(f"Aus Datei/Umgebung: {', '.join(sorted(saved))}")
    return saved


def main() -> None:
    """Ask for credentials, record until the user ends it."""
    if len(sys.argv) > 1:
        print(__doc__)
        return

    saved = saved_credentials()
    serial = saved.get("ECOFLOW_SN") or input("Seriennummer des Wechselrichters: ")
    username = saved.get("ECOFLOW_EMAIL") or input("E-Mail des EcoFlow-Kontos: ")
    password = saved.get("ECOFLOW_PASSWORD") or getpass.getpass("Passwort: ")
    models = ", ".join(f"{code} {name}" for code, name in MODEL_NAME_MAP.items())
    variant = saved.get("ECOFLOW_VARIANT") or input(f"Modell ({models}) [83]: ") or "83"
    serial, username, variant = serial.strip(), username.strip(), variant.strip()

    try:
        user_id, credentials, private = asyncio.run(
            account(EcoflowApi(serial, username, password, variant))
        )
    except EcoflowApiError as err:
        sys.exit(f"Anmeldung fehlgeschlagen: {err}")

    masker = Masker(
        serial,
        user_id,
        [username, password, str(credentials["certificateAccount"]), *private],
    )
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"capture-{datetime.now().astimezone():%Y%m%d-%H%M%S}.jsonl"
    recorder = Recorder(out, masker)
    client = connect(credentials, serial, user_id, recorder)

    print(
        "Notiz eintippen und Enter: markiert, was du gerade in der App machst.\n"
        "q und Enter oder Strg+C: Ende. Angezeigt werden nur die Befehle der App\n"
        "und die Antworten darauf; die Datei enthält alle Nachrichten.\n"
    )
    try:
        while (text := input().strip()).lower() not in STOP:
            if text:
                recorder.note(text)
                print(f"  Notiz: {text}")
    except (KeyboardInterrupt, EOFError):
        pass
    client.disconnect()
    client.loop_stop()
    recorder.close()

    if masker.leaks(out.read_text(encoding="utf-8")):
        out.unlink()
        sys.exit("Eine Seriennummer stand noch in der Datei, sie wurde gelöscht.")
    print(
        f"\n{recorder.count} Einträge in {out.relative_to(ROOT)}\n"
        "Seriennummern und Konto sind maskiert. Bitte deine Notizen vor dem "
        "Hochladen durchsehen."
    )


if __name__ == "__main__":
    main()
