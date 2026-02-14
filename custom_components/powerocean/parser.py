"""
parser.py: EcoFlow API response parser for PowerOcean integration.

This module provides the `EcoflowParser` class, which is responsible for
parsing JSON responses from EcoFlow devices and converting them into
structured PowerOcean endpoints and runtime sensor values.

Key features:
- `EcoflowParser.parse_structure()`: Extracts static device and sensor structure
  without runtime values.
- `EcoflowParser.parse_values()`: Extracts current sensor values from API responses.
- Internal helpers for handling boxed devices (battery, wallbox), parallel
  energy streams, heating rods, and EMS heartbeat data.
- DeviceInfo resolution and unique endpoint ID generation.
- Nested key extraction and Base64 SN decoding.

The module interacts with:
- `StructureCollector` and `ValueCollector` for collecting endpoint metadata
  and runtime values.
- `REPORT_DATAPOINTS` and `BOX_SCHEMAS` for schema-driven parsing.
"""

import base64
import binascii
from typing import Any

import orjson
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util.json import json_loads

from .collector import ReportCollector, StructureCollector, ValueCollector
from .const import (
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PowerOceanModel,
)
from .types import PowerOceanEndPoint
from .utils import (
    BOX_SCHEMAS,
    REPORT_DATAPOINTS,
    BoxSchema,
    ReportMode,
    _join_id,
    decode_version,
)


class EcoflowParser:
    """Parses EcoFlow API responses into structured endpoints and runtime values."""

    def __init__(self, variant: str, sn: str) -> None:
        """
        Initialize the parser for EcoFlow API responses.

        Args:
            variant: EcoFlow device variant / model ID.
            sn: Serial number of the main PowerOcean device.

        """
        self.ecoflow_variant = variant
        self.sn = sn
        self.sn_inverter = sn

    # Public Methods
    def parse_structure(self, response: dict) -> dict[str, PowerOceanEndPoint]:
        """
        Parse the EcoFlow API response and extract the static device structure.

        Args:
            response: The raw JSON response from the EcoFlow API.

        Returns:
            A dictionary mapping unique endpoint IDs to PowerOceanEndPoint objects,
            representing the device and sensor structure without runtime values.

        """
        collector = StructureCollector()
        self._walk_reports(response, collector)
        return collector.endpoints

    def parse_values(self, response: dict) -> dict[str, float | int | str]:
        """
        Parse the EcoFlow API response and extract current sensor values.

        Args:
            response: The raw JSON response from the EcoFlow API.

        Returns:
            A dictionary mapping unique endpoint IDs to their current values.

        """
        collector = ValueCollector()
        self._walk_reports(response, collector)
        return collector.values

    # Internal Methods
    def _walk_reports(self, response: dict, collector: ReportCollector) -> None:
        # error handling for response
        data = response.get("data")
        if not isinstance(data, dict):
            return

        self.sn_inverter = self.sn
        reports_data = list(REPORT_DATAPOINTS.keys())
        reports = []

        # Handle generic 'data' report
        if ReportMode.DEFAULT.value in reports_data:
            self._extract_sensors_from_report(
                response,
                report=ReportMode.DEFAULT.value,
                collector=collector,
            )
            reports = [r for r in reports_data if r != ReportMode.DEFAULT.value]
            LOGGER.debug(f"Reports to look for: {reports}")

        response_parallel = data.get("parallel")
        response_quota = data.get("quota")

        # Dual inverter installation
        if response_parallel:
            inverters = list(response_parallel.keys()) or [self.sn]

            for element in inverters:
                self.sn_inverter = element
                response_base = response_parallel.get(element, {})
                for report in reports:
                    report_key = (
                        ReportMode.PARALLEL.value
                        if ReportMode.ENERGY_STREAM.value in report
                        else report
                    )

                    self._extract_sensors_from_report(
                        response_base,
                        report_key,
                        parallel_energy_stream_mode=ReportMode.PARALLEL.value
                        in report_key,
                        collector=collector,
                    )
        # Single inverter installation
        elif response_quota:
            response_base = response_quota
            for report in reports:
                self._extract_sensors_from_report(
                    response_base,
                    report,
                    collector=collector,
                )
        else:
            LOGGER.warning(
                "Neither 'quota' nor 'parallel' inverter data found in response."
            )
        return

    def _get_device_info(
        self,
        sn: str,
        name: str,
        model: str,
        via_sn: str | None = None,
    ) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            serial_number=sn,
            name=name,
            manufacturer="EcoFlow",
            model=model,
        )
        if via_sn:
            info["via_device"] = (DOMAIN, via_sn)
        return info

    def _parse_battery_data(self, raw_data: dict | str | None) -> dict | None:
        if raw_data is None:
            LOGGER.debug("Battery payload is None (no battery present)")
            return None

        if isinstance(raw_data, dict):
            return raw_data

        if isinstance(raw_data, str):
            try:
                data = json_loads(raw_data)
            except orjson.JSONDecodeError as err:
                LOGGER.warning("Failed to decode battery JSON: %s", err)
                return None

            if isinstance(data, dict):
                return data

            LOGGER.debug(
                "Battery JSON decoded but is %s instead of dict",
                type(data).__name__,
            )
            return None

        LOGGER.debug(
            "Unexpected battery payload type: %s",
            type(raw_data).__name__,
        )
        return None

    def _deep_get_by_key(self, data: Any, target_key: str) -> None:
        """Search recursive for the occurrence of target_key in nested dict/list."""
        if isinstance(data, dict):
            for key, value in data.items():
                # Treffer
                if key == target_key:
                    return value

                # Rekursiv tiefer suchen
                result = self._deep_get_by_key(value, target_key)
                if result is not None:
                    return result

        elif isinstance(data, list):
            for item in data:
                result = self._deep_get_by_key(item, target_key)
                if result is not None:
                    return result
        return None

    @staticmethod
    def _get_nested_value(data: dict[str, Any], path: list[str]) -> Any | None:
        for key in path:
            if not isinstance(data, dict):
                return None
            data = data.get(key)
        return data

    def _detect_box_schema(self, payload: dict) -> tuple[str, BoxSchema] | None:
        for box_type, schema in BOX_SCHEMAS.items():
            detect_fn = schema.get("detect")
            if not callable(detect_fn):
                continue  # Kein detect-Feld, überspringen
            try:
                if detect_fn(payload):
                    return box_type, schema
            except (KeyError, TypeError, AttributeError) as e:
                # Loggen statt blind zu ignorieren
                LOGGER.warning("Error detecting box schema for %s: %s", box_type, e)
                continue
        return None

    def _extract_box_sn(
        self, payload: dict[str, Any], schema: BoxSchema, fallback_sn: str
    ) -> str | None:
        path = schema.get("sn_path")

        sn_value = self._get_nested_value(payload, path) if path else fallback_sn

        # nur strings weitergeben
        sn = sn_value if isinstance(sn_value, str) else None
        if not sn:
            return None

        return self._decode_sn(sn)

    def _extract_box_value(
        self,
        payload: dict,
        key: str,
        schema: BoxSchema,
    ) -> str | None:
        paths = schema.get("paths")
        value = (
            self._get_nested_value(payload, paths[key])
            if paths and key in paths
            else payload.get(key)
        )

        if value is None:
            return None

        if key.endswith("Sn") and isinstance(value, str):
            return self._decode_sn(value)

        return value

    def _make_box_device_info(
        self,
        sn: str,
        schema: BoxSchema,
    ) -> DeviceInfo:
        return self._make_device_info(
            sn=sn,
            prefix=schema["name_prefix"],
            model=schema["model"],
            via_sn=self.sn_inverter,
        )

    @staticmethod
    def _is_matching_report(key: str, report: str) -> bool:
        if not isinstance(key, str):
            return False

        # Spezialfall ENERGY_STREAM_REPORT
        if report == ReportMode.ENERGY_STREAM.value:
            return key.split("_", 1)[1] == report

        # Default-Fall
        return key.endswith(report)

    def _decode_sn(self, value: str | None) -> str | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return base64.b64decode(value, validate=True).decode("utf-8").strip()
        except binascii.Error:
            LOGGER.warning("Invalid base64 string for SN: %s", value)
            return value
        except UnicodeDecodeError:
            return value

    def _resolve_device_info(
        self,
        payload: dict,
    ) -> tuple[str, DeviceInfo]:
        """Resolve serial number and DeviceInfo for non-boxed reports."""
        # bekannte SN-Felder (Reihenfolge = Priorität)
        sn_keys = ("evSn", "hrSn")

        device_sn = self.sn_inverter
        prefix = "PowerOcean"
        model = MODEL_NAME_MAP[PowerOceanModel(self.ecoflow_variant)]
        via_sn = self.sn_inverter

        for key in sn_keys:
            raw_sn = payload.get(key)
            if isinstance(raw_sn, str):
                decoded = self._decode_sn(raw_sn)
                if decoded:
                    device_sn = decoded
                    prefix = "Charger" if key == "evSn" else "Heating Rod"
                    model = f"PowerOcean {prefix}"
                    via_sn = self.sn_inverter
                    break

        device_info = self._make_device_info(
            sn=device_sn,
            prefix=prefix,
            model=model,
            via_sn=via_sn,
        )

        return device_sn, device_info

    def _make_device_info(
        self, sn: str, prefix: str, model: str, via_sn: str | None = None
    ) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            serial_number=sn,
            name=f"{prefix} {sn}",
            manufacturer="EcoFlow",
            model=model,
        )
        if via_sn:
            info["via_device"] = (DOMAIN, via_sn)
        return info

    def _collect_sensor(
        self,
        collector: ReportCollector,
        device_sn: str,
        report: str,
        key: str,
        value: Any,
        device_info: DeviceInfo | None = None,
    ) -> None:
        unique_id = _join_id(device_sn, report, key)
        collector.collect(
            unique_id=unique_id,
            device_sn=device_sn,
            key=key,
            value=value,
            device_info=device_info,
            name=f"{device_sn}_{key}",
            friendly_name=f"{key}",
        )

    def _extract_sensors_from_report(
        self,
        response: dict[str, Any],
        report: str,
        *,
        parallel_energy_stream_mode: bool = False,
        collector: ReportCollector,
    ) -> None:
        """
        Extract sensors from a given report in the EcoFlow API response.

        Args:
            response: The raw JSON response from the EcoFlow API.
            report: The name of the report to extract from the JSON.
            parallel_energy_stream_mode: If True, applies special handling
                for parallel energy streams.
            collector: A collector instance (StructureCollector or ValueCollector)
                used to store extracted endpoints or values.

        Notes:
            Depending on the report type, different handlers may be invoked:
            - Battery / Wallbox reports
            - EMS heartbeat
            - Heating rod energy stream
            - Parallel energy streams
            - Standard report processing

        """
        # Report-Key ggf. anpassen
        report_to_log = report

        key, d = next(
            (
                (k, v)
                for k, v in response.items()
                if self._is_matching_report(k, report)
            ),
            (None, None),
        )
        d = response.get(key) if key else None

        if not isinstance(d, dict):
            LOGGER.debug("Configured report '%s' not in response.", report_to_log)
            return

        sens_select = list(REPORT_DATAPOINTS.get(report, ()))
        # Setze Report-Namen korrekt aus response
        report = key or report
        # Battery und Wallbox Handling
        if ReportMode.BATTERY.value in report or ReportMode.WALLBOX.value in report:
            self._handle_boxed_devices(
                d,
                report=report,
                collector=collector,
            )
            return
        # EMS Heartbeat Mode
        if ReportMode.EMS.value in report:
            self._handle_ems_heartbeat_mode(
                d,
                report,
                sens_select,
                collector=collector,
            )
            return

        # Heating Rod Energy Stream Mode
        if ReportMode.HEATING_ROD_ENERGY.value in report:
            self._handle_heating_rod_energy_stream(
                d,
                report,
                sens_select=sens_select,
                collector=collector,
            )
            return

        if ReportMode.WALLBOX_SYS.value in report:
            self._handle_edev_device(
                d,
                report=report,
                sens_select=sens_select,
                collector=collector,
            )
            return

        # Parallel Energy Stream Mode
        if parallel_energy_stream_mode:
            self._handle_parallel_energy_stream(
                d,
                report,
                collector,
            )
        # Standardverarbeitung
        self._handle_standard_mode(d, report, sens_select, collector=collector)

    def _handle_boxed_devices(
        self,
        d: dict,
        *,
        report: str,
        collector: ReportCollector,
    ) -> None:
        for box_sn_raw, raw_payload in d.items():
            if box_sn_raw in ("", "updateTime"):
                continue

            payload = self._parse_battery_data(raw_payload)
            if not isinstance(payload, dict):
                continue

            detected = self._detect_box_schema(payload)

            if not detected:
                LOGGER.debug("Unknown boxed device schema")
                continue

            # box_type wird aktuell nicht genutzt
            _, schema = detected

            device_sn = self._extract_box_sn(payload, schema, box_sn_raw)
            if not device_sn:
                continue

            device_info = self._make_box_device_info(device_sn, schema)

            for key in schema["sensors"]:
                value = self._extract_box_value(payload, key, schema)
                if value is None:
                    continue

                if key == "moduleAplSwVer":
                    value = decode_version(value)
                self._collect_sensor(
                    collector=collector,
                    device_sn=device_sn,
                    report=report,
                    key=key,
                    value=value,
                    device_info=device_info,
                )

    def _handle_ems_heartbeat_mode(
        self,
        d: dict,
        report: str,
        sens_select: list,
        collector: ReportCollector,
    ) -> None:
        device_info = self._make_device_info(
            sn=self.sn_inverter,
            prefix="PowerOcean",
            model=MODEL_NAME_MAP[PowerOceanModel(self.ecoflow_variant)],
            via_sn=self.sn_inverter,
        )

        # EMS Heartbeat: ggf. verschachtelte Strukturen, spezielle Behandlung
        for key, value in d.items():
            if key in sens_select:
                self._collect_sensor(
                    collector,
                    self.sn_inverter,
                    report,
                    key,
                    value,
                    device_info=device_info,
                )
        # Besonderheiten Phasen
        phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
        if all(phase in d for phase in phases):
            for _, phase in enumerate(phases):
                for key, value in d[phase].items():
                    key_ext = f"{phase}_{key}"
                    self._collect_sensor(
                        collector,
                        self.sn_inverter,
                        report,
                        key_ext,
                        value,
                        device_info=device_info,
                    )

        # Besonderheit mpptPv
        if "mpptHeartBeat" in d:
            n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])
            report = f"{report}_mpptHeartBeat"
            for i in range(n_strings):
                for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                    key_ext = f"mpptPv{i + 1}_{key}"
                    self._collect_sensor(
                        collector,
                        self.sn_inverter,
                        report,
                        key_ext,
                        value,
                        device_info=device_info,
                    )

            # Gesamtleistung mpptPv
            total_power = sum(
                d["mpptHeartBeat"][0]["mpptPv"][i].get("pwr", 0)
                for i in range(n_strings)
            )
            key_ext = "mpptPv_pwrTotal"
            self._collect_sensor(
                collector,
                self.sn_inverter,
                report,
                key_ext,
                total_power,
                device_info=device_info,
            )

            # --- Isolationswiderstand ---
            mppt_ins_resist = d["mpptHeartBeat"][0].get("mpptInsResist")
            if mppt_ins_resist is not None:
                self._collect_sensor(
                    collector,
                    self.sn_inverter,
                    report,
                    "mpptInsResist",
                    mppt_ins_resist,
                    device_info=device_info,
                )

    def _handle_heating_rod_energy_stream(
        self,
        d: dict,
        report: str,
        sens_select: list,
        collector: ReportCollector,
    ) -> None:
        """Handle heating rod energy stream extraction."""
        stream_list = d.get("hrEnergyStream")
        if not isinstance(stream_list, list):
            return

        for element in stream_list:
            if not isinstance(element, dict):
                continue

            raw_sn = element.get("hrSn")
            device_sn = self._decode_sn(raw_sn) if raw_sn else None
            if not device_sn:
                continue

            device_info = self._make_device_info(
                sn=device_sn,
                prefix="PowerGlow",
                model="PowerOcean PowerGlow",
                via_sn=self.sn_inverter,
            )

            for key, value in element.items():
                if isinstance(value, dict):
                    continue

                if key in sens_select:
                    self._collect_sensor(
                        collector=collector,
                        device_sn=device_sn,
                        report=report,
                        key=key,
                        value=value,
                        device_info=device_info,
                    )

    def _handle_parallel_energy_stream(
        self,
        d: dict,
        report: str,
        collector: ReportCollector,
    ) -> None:
        """Handle parallel energy stream data extraction."""
        para_list = d.get("paraEnergyStream", [])
        if not isinstance(para_list, list):
            LOGGER.warning("paraEnergyStream is not a list")
            return
        report = f"{report}_paraEnergyStream"
        for device_data in para_list:
            raw_sn = device_data.get("devSn")
            device_sn = self._decode_sn(raw_sn) if raw_sn else None

            # Wenn keine SN vorhanden → aggregierter Wert
            if not device_sn:
                device_sn = f"{self.sn}_all"

            # DeviceInfo
            prefix = "Inverter"
            device_info = self._make_device_info(
                sn=device_sn,
                prefix=prefix,
                model=f"PowerOcean {prefix}",
                via_sn=self.sn_inverter if not device_sn.endswith("_all") else None,
            )
            for key, value in device_data.items():
                if isinstance(value, dict):
                    continue
                if key.endswith("Sn") and isinstance(value, str):
                    self._decode_sn(value)
                self._collect_sensor(
                    collector=collector,
                    device_sn=device_sn,
                    report=report,
                    key=key,
                    value=value,
                    device_info=device_info,
                )

    def _handle_standard_mode(
        self,
        d: dict,
        report: str,
        sens_select: list,
        collector: ReportCollector,
    ) -> None:
        # spezielle Behandlung für 'data' Report
        report_id = "" if report == ReportMode.DEFAULT.value else f"{report}"
        device_sn, device_info = self._resolve_device_info(d)

        for key, raw_value in d.items():
            if key not in sens_select:
                continue
            if isinstance(raw_value, dict):
                continue

            value = raw_value
            self._collect_sensor(
                collector=collector,
                device_sn=device_sn,
                report=report_id,
                key=key,
                value=value,
                device_info=device_info,
            )

    def _handle_edev_device(
        self,
        d: dict,
        report: str,
        sens_select: list,
        collector: ReportCollector,
    ) -> None:
        """Handle for RE307_EDEV_SYS_REPORT."""
        # SN über Deep Search holen
        device_sn = self._deep_get_by_key(d, "devSn")

        if not device_sn:
            return

        device_info = self._make_device_info(
            sn=device_sn,
            prefix="PowerPulse",
            model="PowerOcean PowerPulse",
            via_sn=self.sn_inverter,
        )

        # sens_select generisch auflösen
        for key in sens_select:
            value = device_sn if key == "devSn" else self._deep_get_by_key(d, key)

            if key == "devSn":
                continue
            if value is None:
                continue

            # nur primitive Werte erlauben
            if isinstance(value, (dict, list)):
                continue

            self._collect_sensor(
                collector=collector,
                device_sn=device_sn,
                report=report,
                key=key,
                value=value,
                device_info=device_info,
            )
