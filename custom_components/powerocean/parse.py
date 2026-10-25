"""
parse.py: Parse an EcoFlow PowerOcean API response into datapoints.

One pass over the response yields every datapoint with its device and
value. `HANDLERS` sets which function reads which report; `REPORT_DATAPOINTS`
and `BOX_SCHEMAS` (datapoints.py) set which keys are read.

The unique_id of a datapoint is `{device_sn}_{report}_{key}`, with the full
report name as it appears in the response (e.g. `JTS1_EMS_HEARTBEAT`).
Entities, history and automations depend on it, so it must never change.

This module does not depend on Home Assistant.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import orjson

from .datapoints import (
    BOX_SCHEMAS,
    REPORT_DATAPOINTS,
    UPDATE_TIME_KEYS,
    VALUE_SCALE,
    BoxSchema,
    ReportMode,
)
from .devices import (
    LOGGER,
    MODEL_NAME_MAP,
    Device,
    PowerOceanModel,
    decode_sn,
    make_device,
)
from .errors import ActiveError, parse_error_report
from .flows import energy_flows

PRIMITIVE_TYPES = (int, float, str)

# "updateTime" is the time of the EcoFlow server (UTC+8, no daylight saving
# time): in all fixtures it is exactly 8 hours ahead of the battery's
# bpTimestamp (Unix time), in winter and summer alike.
SERVER_TIMEZONE = timezone(timedelta(hours=8))

# Power of PV string n in the energy stream report
PV_STRING_POWER = re.compile(r"^pv(\d+)Pwr$")


def _join_id(*parts: str) -> str:
    return "_".join(p for p in parts if p)


def decode_version(value: int) -> str:
    """
    Decode a 32-bit integer into a dotted version string.

    The integer is interpreted as:
        - bits 24-31: major
        - bits 16-23: minor
        - bits 8-15: patch
        - bits 0-7: build

    Args:
        value: Encoded 32-bit version integer.

    Returns:
        Version string in the format "major.minor.patch.build".

    """
    major = (value >> 24) & 0xFF
    minor = (value >> 16) & 0xFF
    patch = (value >> 8) & 0xFF
    build = value & 0xFF
    return f"{major}.{minor}.{patch}.{build}"


@dataclass(frozen=True)
class Datapoint:
    """A value of a device, as found in the response."""

    unique_id: str
    device: Device
    key: str
    value: Any
    # False if the system lacks what the value is about (e.g. a PV string)
    in_use: bool = True


@dataclass
class ParseResult:
    """All datapoints of a response, in the order they were found."""

    # A unique_id can occur more than once (the same report read twice)
    datapoints: list[Datapoint] = field(default_factory=list)
    # Active errors, by unique_id of the error count datapoint
    active_errors: dict[str, list[ActiveError]] = field(default_factory=dict)
    # Firmware version, by serial number of the device
    firmware: dict[str, str] = field(default_factory=dict)

    def first(self) -> dict[str, Datapoint]:
        """Return the first datapoint per unique_id, in the order found."""
        first: dict[str, Datapoint] = {}
        for datapoint in self.datapoints:
            first.setdefault(datapoint.unique_id, datapoint)
        return first

    def values(self) -> dict[str, float | int | str]:
        """Return the last primitive value per unique_id."""
        return {
            datapoint.unique_id: datapoint.value
            for datapoint in self.datapoints
            if isinstance(datapoint.value, PRIMITIVE_TYPES)
        }


@dataclass
class _Context:
    """State of one parse run."""

    main_sn: str
    model: str
    result: ParseResult
    # Inverter whose reports are read (differs from main_sn in dual systems)
    inverter_sn: str = ""
    # Number of PV strings of the inverter, if the heartbeat tells
    pv_strings: int | None = None

    def inverter(self) -> Device:
        return make_device(self.inverter_sn, "PowerOcean", self.model)

    def add(self, device: Device, report: str, key: str, value: Any) -> None:
        scale = VALUE_SCALE.get(key)
        if scale and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = round(value * scale, 1)
        self.result.datapoints.append(
            Datapoint(
                unique_id=_join_id(device.sn, report, key),
                device=device,
                key=key,
                value=value,
                in_use=self._in_use(key),
            )
        )

    def _in_use(self, key: str) -> bool:
        # pv3Pwr of the energy stream is 0 forever without a third string
        match = PV_STRING_POWER.match(key)
        return not (
            match and self.pv_strings is not None and int(match[1]) > self.pv_strings
        )


def parse(response: dict[str, Any], main_sn: str, variant: str) -> ParseResult:
    """
    Parse an API response.

    Args:
        response: The raw JSON response from the EcoFlow API.
        main_sn: Serial number of the main inverter (from the config entry).
        variant: Product type, e.g. "83" for PowerOcean.

    Raises:
        ValueError: If the variant is unknown.

    """
    result = ParseResult()
    data = response.get("data")
    if not isinstance(data, dict):
        return result

    ctx = _Context(
        main_sn=main_sn,
        model=MODEL_NAME_MAP[PowerOceanModel(variant)],
        result=result,
        inverter_sn=main_sn,
    )

    # Fields directly under "data": unique_id without report name
    _read_standard(
        ctx, ReportMode.DEFAULT.value, data, REPORT_DATAPOINTS[ReportMode.DEFAULT.value]
    )

    report_types = [r for r in REPORT_DATAPOINTS if r != ReportMode.DEFAULT.value]
    parallel = data.get("parallel")
    quota = data.get("quota")

    if parallel:
        # Dual installation: the reports of each inverter
        for inverter_sn in list(parallel.keys()) or [main_sn]:
            ctx.inverter_sn = inverter_sn
            reports = parallel.get(inverter_sn, {})
            ctx.pv_strings = _pv_strings(reports)
            for report_type in report_types:
                # KNOWN BUG, kept until fixed in its own commit: the substring
                # check also replaces HEATING_ROD_ENERGY_STREAM_REPORT, so the
                # heating rod's energy flows are missing in dual systems.
                if ReportMode.ENERGY_STREAM.value in report_type:
                    report_type = ReportMode.PARALLEL.value  # noqa: PLW2901
                _read_report(ctx, reports, report_type)
    elif quota:
        ctx.pv_strings = _pv_strings(quota)
        for report_type in report_types:
            _read_report(ctx, quota, report_type)
    else:
        LOGGER.warning(
            "Neither 'quota' nor 'parallel' inverter data found in response."
        )

    return result


# -----------------------------
# Finding reports
# -----------------------------


def _pv_strings(reports: Any) -> int | None:
    """Return the number of PV strings listed in the heartbeat, if any."""
    if not isinstance(reports, dict):
        return None
    _, heartbeat = _find_report(reports, ReportMode.EMS.value)
    mppt = _first_mppt_heartbeat(heartbeat)
    strings = mppt.get("mpptPv") if mppt else None
    return len(strings) if isinstance(strings, list) else None


def _matches(key: Any, report_type: str) -> bool:
    """Return True if the response key `key` is a report of `report_type`."""
    if not isinstance(key, str):
        return False
    # Only the plain energy stream report, not EMS_PV_INV_ENERGY_STREAM_REPORT etc.
    if report_type == ReportMode.ENERGY_STREAM.value:
        return key.partition("_")[2] == report_type
    return key.endswith(report_type)


def _find_report(
    reports: dict[str, Any], report_type: str
) -> tuple[str | None, dict[str, Any]]:
    """Return name and payload of the first report of `report_type`."""
    for key, value in reports.items():
        if _matches(key, report_type):
            return key, value if isinstance(value, dict) else {}
    return None, {}


def _read_report(ctx: _Context, reports: dict[str, Any], report_type: str) -> None:
    name, payload = _find_report(reports, report_type)

    if report_type == ReportMode.EMS_CHANGE.value:
        # Newer firmware moved some values to EMS_STATE_CHANGE_REPORT; they are
        # read as part of EMS_CHANGE_REPORT, so their unique_ids stay the same
        payload = dict(payload)
        selected = REPORT_DATAPOINTS[ReportMode.EMS_CHANGE.value]
        for key in reports:
            if not key.endswith(ReportMode.EMS_STATE_CHANGE.value):
                continue
            state = reports.get(key)
            if isinstance(state, dict):
                for state_key in selected:
                    if state_key in state:
                        payload[state_key] = state[state_key]

    handler = HANDLERS.get(report_type, _read_standard)
    handler(ctx, name or report_type, payload, REPORT_DATAPOINTS.get(report_type, ()))

    time_key = UPDATE_TIME_KEYS.get(report_type)
    if name and time_key and (time := _server_time(payload.get("updateTime"))):
        ctx.add(ctx.inverter(), name, time_key, time)


def _server_time(value: Any) -> int | None:
    """Return a server time such as "2026-10-18 06:42:15" as Unix time."""
    if not isinstance(value, str):
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
    except ValueError:
        return None
    return int(local.replace(tzinfo=SERVER_TIMEZONE).timestamp())


# -----------------------------
# Handlers
# -----------------------------


def _read_standard(
    ctx: _Context, name: str, payload: dict[str, Any], selected: Any
) -> None:
    """Read the selected flat values of a report."""
    report = "" if name == ReportMode.DEFAULT.value else name
    device = _payload_device(ctx, payload)
    for key, value in payload.items():
        if key not in selected or isinstance(value, dict):
            continue
        ctx.add(device, report, key, value)


def _payload_device(ctx: _Context, payload: dict[str, Any]) -> Device:
    """Return the charger or heating rod a report belongs to, else the inverter."""
    # Same name as the other reports of these devices, so the device name and
    # the entity IDs do not depend on which report comes first
    for sn_key, prefix in (("evSn", "PowerPulse"), ("hrSn", "PowerGlow")):
        raw_sn = payload.get(sn_key)
        if isinstance(raw_sn, str) and (sn := decode_sn(raw_sn)):
            return make_device(sn, prefix, f"PowerOcean {prefix}", ctx.inverter_sn)
    return ctx.inverter()


def _read_boxed(
    ctx: _Context,
    name: str,
    payload: dict[str, Any],
    selected: Any,  # noqa: ARG001 (the box schema selects the keys)
) -> None:
    """Read devices listed by serial number, such as battery packs."""
    for box_key, raw_box in payload.items():
        if box_key in ("", "updateTime"):
            continue
        box = _load_box(raw_box)
        if box is None:
            continue
        schema = _detect_box_schema(box)
        if schema is None:
            LOGGER.debug("Unknown boxed device schema")
            continue
        sn = _box_sn(box, schema)
        if not sn:
            continue

        device = make_device(
            sn, schema["name_prefix"], schema["model"], ctx.inverter_sn
        )
        for key in schema["sensors"]:
            value = _box_value(box, key, schema)
            if value is None:
                continue
            ctx.add(device, name, key, value)
        # Shown as firmware on the device page, not as a sensor
        version = _box_value(box, "moduleAplSwVer", schema)
        if isinstance(version, int):
            ctx.result.firmware[sn] = decode_version(version)


def _load_box(raw: Any) -> dict[str, Any] | None:
    """Return a box payload, given as dict or as JSON string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        box = orjson.loads(raw)
    except orjson.JSONDecodeError as err:
        LOGGER.warning("Failed to decode battery JSON: %s", err)
        return None
    return box if isinstance(box, dict) else None


def _detect_box_schema(box: dict[str, Any]) -> BoxSchema | None:
    for schema in BOX_SCHEMAS.values():
        if schema["detect"](box):
            return schema
    return None


def _box_sn(box: dict[str, Any], schema: BoxSchema) -> str | None:
    sn = _nested_value(box, schema["sn_path"])
    return decode_sn(sn) if isinstance(sn, str) and sn else None


def _box_value(box: dict[str, Any], key: str, schema: BoxSchema) -> Any:
    paths = schema["paths"]
    value = _nested_value(box, paths[key]) if paths and key in paths else box.get(key)
    if key.endswith("Sn") and isinstance(value, str):
        return decode_sn(value)
    return value


def _nested_value(data: Any, path: list[str]) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _read_ems_heartbeat(
    ctx: _Context, name: str, payload: dict[str, Any], selected: Any
) -> None:
    """Read the inverter's live values, phases, MPPT strings and energy flows."""
    device = ctx.inverter()
    for key, value in payload.items():
        if key in selected:
            ctx.add(device, name, key, value)

    # Phases: pcsAPhase_vol, pcsAPhase_amp, ...
    phases = ("pcsAPhase", "pcsBPhase", "pcsCPhase")
    if all(phase in payload for phase in phases):
        for phase in phases:
            if isinstance(payload[phase], dict):
                for key, value in payload[phase].items():
                    ctx.add(device, name, f"{phase}_{key}", value)

    mppt = _first_mppt_heartbeat(payload)
    if mppt is not None:
        _read_mppt(ctx, device, f"{name}_mpptHeartBeat", mppt, payload)

    _read_meters(ctx, f"{name}_meterHeartBeat", payload, selected)


# Three-phase smart meter: meterData is [L1, L2, L3, total] in W. The other
# types seen (1, 13, 15) report zeros or the total only; they are skipped
# until their meaning is known.
METER_TYPE_THREE_PHASE = 11
METER_KEYS = ("meterL1Pwr", "meterL2Pwr", "meterL3Pwr", "meterTotalPwr")


def _read_meters(
    ctx: _Context, report: str, payload: dict[str, Any], selected: Any
) -> None:
    """Read the smart meters, each as its own device."""
    meters = payload.get("meterHeartBeat")
    if not isinstance(meters, list):
        return
    for meter in meters:
        if not isinstance(meter, dict):
            continue
        address = meter.get("meterAddr")
        data = meter.get("meterData")
        if (
            meter.get("meterType") != METER_TYPE_THREE_PHASE
            or not isinstance(address, int)
            or not isinstance(data, list)
        ):
            continue
        # The meter has no serial number of its own, so its name has none
        device = Device(
            sn=f"{ctx.inverter_sn}_meter{address}",
            name="Smart Meter",
            model="PowerOcean Smart Meter",
            via_sn=ctx.inverter_sn,
        )
        for key, value in zip(METER_KEYS, data, strict=False):
            if key not in selected or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                ctx.add(device, report, key, value)


def _read_mppt(
    ctx: _Context,
    device: Device,
    report: str,
    mppt: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Read the MPPT strings and calculate the energy flows."""
    # Strings, numbered by their position in the list
    strings = mppt.get("mpptPv")
    if not isinstance(strings, list):
        strings = []
    for number, string in enumerate(strings, start=1):
        if isinstance(string, dict):
            for key, value in string.items():
                ctx.add(device, report, f"mpptPv{number}_{key}", value)

    solar_total = sum(
        power
        for string in strings
        if isinstance(string, dict)
        and isinstance(power := string.get("pwr", 0), (int, float))
    )
    ctx.add(device, report, "mpptPv_pwrTotal", solar_total)

    if (resistance := mppt.get("mpptInsResist")) is not None:
        ctx.add(device, report, "mpptInsResist", resistance)

    for key, value in energy_flows(
        solar_total, payload.get("pcsMeterPower"), payload.get("emsBpPower")
    ):
        ctx.add(device, report, key, value)


def _first_mppt_heartbeat(payload: dict[str, Any]) -> dict[str, Any] | None:
    mppt_list = payload.get("mpptHeartBeat")
    if isinstance(mppt_list, list) and mppt_list and isinstance(mppt_list[0], dict):
        return mppt_list[0]
    return None


def _read_heating_rod_energy(
    ctx: _Context, name: str, payload: dict[str, Any], selected: Any
) -> None:
    """Read the energy flows of each heating rod."""
    streams = payload.get("hrEnergyStream")
    if not isinstance(streams, list):
        return
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        raw_sn = stream.get("hrSn")
        sn = decode_sn(raw_sn) if raw_sn else None
        if not sn:
            continue
        device = make_device(sn, "PowerGlow", "PowerOcean PowerGlow", ctx.inverter_sn)
        for key, value in stream.items():
            if not isinstance(value, dict) and key in selected:
                ctx.add(device, name, key, value)


def _read_errors(
    ctx: _Context,
    name: str,
    payload: dict[str, Any],
    selected: Any,  # noqa: ARG001 (the error count is calculated)
) -> None:
    """Count the active errors of all modules of the inverter."""
    if not payload:
        return
    errors = parse_error_report(payload)
    device = ctx.inverter()
    ctx.result.active_errors[_join_id(device.sn, name, "errorCount")] = errors
    ctx.add(device, name, "errorCount", len(errors))


def _read_edev_system(
    ctx: _Context, name: str, payload: dict[str, Any], selected: Any
) -> None:
    """Read the energy management values of the external device (wallbox)."""
    # KNOWN LIMITATION: the report lists several devices, only the first is read
    sn = _first_value(payload, "devSn")
    if not sn:
        return
    device = make_device(sn, "PowerPulse", "PowerOcean PowerPulse", ctx.inverter_sn)
    for key in selected:
        if key == "devSn":
            continue
        value = _first_value(payload, key)
        if value is None or isinstance(value, (dict, list)):
            continue
        ctx.add(device, name, key, value)


def _first_value(data: Any, target_key: str) -> Any:
    """Return the first value of `target_key` in nested dicts and lists."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == target_key:
                return value
            result = _first_value(value, target_key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _first_value(item, target_key)
            if result is not None:
                return result
    return None


def _read_parallel_energy_stream(
    ctx: _Context,
    name: str,
    payload: dict[str, Any],
    selected: Any,  # noqa: ARG001 (all keys are read)
) -> None:
    """Read the energy streams of each inverter of a dual installation."""
    streams = payload.get("paraEnergyStream", [])
    if not isinstance(streams, list):
        LOGGER.warning("paraEnergyStream is not a list")
        return
    report = f"{name}_paraEnergyStream"
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        raw_sn = stream.get("devSn")
        # Without serial number: the sum of all inverters
        sn = (decode_sn(raw_sn) if raw_sn else None) or f"{ctx.main_sn}_all"
        via_sn = ctx.main_sn if sn != ctx.main_sn else None
        device = make_device(sn, "Inverter", "PowerOcean Inverter", via_sn)
        for key, value in stream.items():
            if not isinstance(value, dict):
                ctx.add(device, report, key, value)


Handler = Callable[[_Context, str, dict[str, Any], Any], None]

# Reports not listed here are read by `_read_standard`
HANDLERS: dict[str, Handler] = {
    ReportMode.BATTERY.value: _read_boxed,
    ReportMode.WALLBOX.value: _read_boxed,
    ReportMode.EMS.value: _read_ems_heartbeat,
    ReportMode.HEATING_ROD_ENERGY.value: _read_heating_rod_energy,
    ReportMode.ERROR.value: _read_errors,
    ReportMode.WALLBOX_SYS.value: _read_edev_system,
    ReportMode.PARALLEL.value: _read_parallel_energy_stream,
}
