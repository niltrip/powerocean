"""ecoflow.py: API for PowerOcean integration."""

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
import requests
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util.json import json_loads

from .const import (
    _LOGGER,
    BOX_SCHEMAS,
    DOMAIN,
    ISSUE_URL_ERROR_MESSAGE,
    MOCKED_RESPONSE,
    USE_MOCKED_RESPONSE,
    BoxSchema,
    DeviceRole,
    ReportMode,
    SensorMetaHelper,
)


# Better storage of PowerOcean endpoint
@dataclass
class PowerOceanEndPoint:
    """
    Represents a PowerOcean endpoint with metadata and value.

    Attributes:
        internal_unique_id (str): Unique identifier for the endpoint.
        serial (str): Serial number of the device.
        name (str): Name of the endpoint.
        friendly_name (str): Human-readable name.
        value (object): Value of the endpoint.
        unit (str | None): Unit of measurement.
        description (str): Description of the endpoint.
        icon (str | None): Icon representing the endpoint.
        device_info (DeviceInfo): Inverter/Battery/Wallbox.

    """

    internal_unique_id: str
    serial: str
    name: str
    friendly_name: str
    value: object
    unit: str | None
    description: str
    icon: str | None
    device_info: DeviceInfo | None = None


# ecoflow_api to detect device and get device info,
# fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow."""

    def __init__(
        self,
        serialnumber: str,
        username: str,
        password: str,
        variant: str,
        options: dict,
    ) -> None:
        """
        Initialize the Ecoflow API integration.

        Args:
            serialnumber (str): The serial number of the device.
            username (str): The Ecoflow account username.
            password (str): The Ecoflow account password.
            variant (str): The device variant.
            options (dict): Additional options for configuration.

        """
        self.sn = serialnumber
        self.sn_inverter = ""
        self.unique_id = serialnumber
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.ecoflow_variant = variant
        self.token = None
        self.device = None
        self.session = requests.Session()
        self.url_iot_app = "https://api.ecoflow.com/auth/login"
        self.url_user_fetch = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        # Path relative to this file (ecoflow.py)
        base_path = Path(__file__).parent
        self.datapointfile = base_path / "variants" / f"{self.ecoflow_variant}.json"
        self.options = options  # Store Home Assistant instance
        self._json_data_variant = None

    def get_device(self) -> dict:
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.33",  # Version vom Author.
            "build": "19",  # Version vom Author.
            "name": f"Inverter {self.sn}",
            "features": "Photovoltaik",
        }

        return self.device

    def authorize(self) -> bool:
        """Authorize user and retrieve authentication token."""
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.ecoflow_username,
            "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        url = self.url_iot_app
        _LOGGER.info(f"Attempting to log in to EcoFlow API: {url}")
        response_data = None

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response_data = self.get_json_response(response)

            _LOGGER.debug(f"API Response: {response_data}")
            data_block = response_data.get("data")
            if not isinstance(data_block, dict) or "token" not in data_block:
                msg = "Missing or malformed 'data' block in response"
                _LOGGER.error(msg)
                raise AuthenticationFailedError(msg)

            self.token = data_block["token"]

        except requests.exceptions.RequestException as err:
            error_msg = f"Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error_msg + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error_msg) from err

        except KeyError as key_error:
            _LOGGER.error(
                f"Failed to extract key {key_error} from response: {response_data}"
            )
            msg = f"Missing {key_error} in response"
            raise FailedToExtractKeyError(msg, response_data or {}) from None

        _LOGGER.info("Successfully logged in.")
        self.get_device()
        return True

    def get_json_response(self, response: requests.Response) -> dict:
        """Parse JSON response and validate structure and status."""
        if not response.ok:
            msg = f"HTTP {response.status_code}: {response.text}"
            raise ApiResponseError(msg)

        try:
            response_data = json_loads(response.text)
        except ValueError as error:
            msg = f"Failed to parse JSON: {response.text} — Error: {error}"
            raise json_loads.JsonParseError(msg) from error

        if not isinstance(response_data, dict) or "message" not in response_data:
            msg = f"'message' key missing in response: {response_data}"
            raise KeyError(msg)

        message = response_data.get("message")
        if not isinstance(message, str) or message.lower() != "success":
            msg = f"API Error: {message}"
            raise ApiResponseError(msg)

        if "data" not in response_data:
            msg = f"'data' key missing or invalid: {response_data}"
            raise KeyError(msg)

        return response_data

    # Fetch the data from the PowerOcean device, which then constitues the Sensors
    def fetch_data(self) -> dict:
        """Fetch data from Url."""
        url = self.url_user_fetch
        try:
            headers = {
                "authorization": f"Bearer {self.token}",
                "user-agent": "Firefox/133.0",
                "product-type": self.ecoflow_variant,
            }
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)

            if USE_MOCKED_RESPONSE:
                if MOCKED_RESPONSE.exists():
                    try:
                        response = json_loads(
                            MOCKED_RESPONSE.read_text(encoding="utf-8")
                        )
                    except (UnicodeDecodeError, orjson.JSONDecodeError) as err:
                        _LOGGER.error(f"Failed to load mocked response: {err}")
                else:
                    _LOGGER.debug(f"Mocked response file not found: {MOCKED_RESPONSE}")

            # Log the response for debugging or development purposes
            _LOGGER.debug(f"{response}")
            flattened_data = Ecoflow.flatten_json(response)  # noqa: F841
            # _LOGGER.debug(f"Flattened data: {flattened_data}")  # noqa: ERA001

            # Ensure response is a dictionary before passing to _get_sensors
            if isinstance(response, dict):
                return self._get_sensors(response)
            raise ResponseTypeError(type(response).__name__)

        except ConnectionError as err:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error) from err

    def _get_device_info(
        self,
        sn: str,
        *,
        name: str,
        model: str,
        via_sn: str | None = None,
    ) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            name=name,
            manufacturer="EcoFlow",
            model=model,
        )
        if via_sn:
            info["via_device"] = (DOMAIN, via_sn)
        return info

    def __read_json_file(self) -> dict:
        # Hier wird die Datei eingelesen, wenn die Daten noch nicht geladen sind
        try:
            if self._json_data_variant is None:
                with self.datapointfile.open("r", encoding="utf-8") as file:
                    self._json_data_variant = json_loads(file.read())
                    if isinstance(self._json_data_variant, dict):
                        return self._json_data_variant
                    _LOGGER.error(
                        f"JSON content is not a dict in file: {self.datapointfile}"
                    )
                    return {}
        except FileNotFoundError:
            _LOGGER.error(f"File not found: {self.datapointfile}")
        except orjson.JSONDecodeError:
            _LOGGER.error(f"Error decoding JSON in file: {self.datapointfile}")
        return {}

    def _get_reports(self) -> list[str]:
        schema = self._load_schema()
        return list(schema.keys())

    def _get_sens_select(self, report: str) -> list[str]:
        schema = self._load_schema()
        value = schema.get(report)
        return value if isinstance(value, list) else []

    def _parse_battery_data(self, raw_data: dict | str | None) -> dict | None:
        if raw_data is None:
            _LOGGER.debug("Battery payload is None (no battery present)")
            return None

        if isinstance(raw_data, dict):
            return raw_data

        if isinstance(raw_data, str):
            try:
                data = json_loads(raw_data)
            except orjson.JSONDecodeError as err:
                _LOGGER.warning("Failed to decode battery JSON: %s", err)
                return None

            if isinstance(data, dict):
                return data

            _LOGGER.debug(
                "Battery JSON decoded but is %s instead of dict",
                type(data).__name__,
            )
            return None

        _LOGGER.debug(
            "Unexpected battery payload type: %s",
            type(raw_data).__name__,
        )
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
                _LOGGER.warning("Error detecting box schema for %s: %s", box_type, e)
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

    def _load_schema(self) -> dict:
        if not hasattr(self, "_schema"):
            self._schema = self.__read_json_file() or {}
        return self._schema

    def _get_box_sensors(
        self,
        box_schema: BoxSchema,
    ) -> list[str]:
        """Liefert Sensoren die der Box-Typ unterstützt."""
        return list(box_schema["sensors"])

    def _create_sensor(self, endpoint: PowerOceanEndPoint) -> PowerOceanEndPoint:
        return endpoint

    def _get_sensors(self, response: dict) -> dict:
        sensors = {}  # start with empty dict
        self.sn_inverter = self.sn
        # error handling for response
        data = response.get("data")
        if not isinstance(data, dict):
            _LOGGER.error("No 'data' in response.")
            return sensors  # return empty dict if no data

        # Handle generic 'data' report
        reports_data = self._get_reports()
        reports = []
        report = ReportMode.DEFAULT.value
        if report in reports_data:
            # Special case for 'data' report
            sensors.update(
                self._extract_sensors_from_report(
                    response,
                    sensors,
                    report,
                )
            )
            reports = [x for x in reports_data if x != "data"]
        _LOGGER.debug(f"Reports to look for: {reports}")

        response_parallel = data.get("parallel")
        response_quota = data.get("quota")

        # Dual inverter installation
        if response_parallel:
            inverters = list(response_parallel.keys()) or [self.sn]

            for element in inverters:
                self.sn_inverter = element
                response_base = response_parallel.get(element, {})
                suffix = (
                    DeviceRole.MASTER.value
                    if element == self.sn
                    else DeviceRole.SLAVE.value
                )
                for report in reports:
                    report_key = (
                        ReportMode.PARALLEL.value
                        if ReportMode.ENERGY_STREAM.value in report
                        else report
                    )

                    sensors.update(
                        self._extract_sensors_from_report(
                            response_base,
                            sensors,
                            report_key,
                            suffix=suffix,
                            battery_mode=ReportMode.BATTERY.value in report_key,
                            wallbox_mode=ReportMode.WALLBOX.value in report_key,
                            ems_heartbeat_mode=ReportMode.EMS.value in report_key,
                            parallel_energy_stream_mode=ReportMode.PARALLEL.value
                            in report_key,
                        )
                    )
        # Single inverter installation
        elif response_quota:
            response_base = response_quota
            for report in reports:
                sensors.update(
                    self._extract_sensors_from_report(
                        response_base,
                        sensors,
                        report,
                        battery_mode=ReportMode.BATTERY.value in report,
                        wallbox_mode=ReportMode.WALLBOX.value in report,
                        ems_heartbeat_mode=ReportMode.EMS.value in report,
                    )
                )
        else:
            _LOGGER.warning(
                "Neither 'quota' nor 'parallel' inverter data found in response."
            )
        return sensors

    def _extract_sensors_from_report(  # noqa: PLR0913
        self,
        response: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        suffix: str = "",
        *,
        battery_mode: bool = False,
        wallbox_mode: bool = False,
        ems_heartbeat_mode: bool = False,
        parallel_energy_stream_mode: bool = False,
    ) -> dict[str, PowerOceanEndPoint]:
        """
        Allgemeine Methode zum Extrahieren von Sensoren aus einem Report.

        Args:
            response: API Antwort als dict
            sensors: bisher gesammelte Sensoren (wird erweitert)
            report: Name des Reports im JSON
            suffix: Suffix, das an die Namen der Sensoren angehängt wird
                (z.B. für Master/Slave).
            battery_mode: Wenn True, werden Batteriedaten speziell behandelt
            wallbox_mode: Wenn True, werden Wallboxdaten speziell behandelt
            chargebox_mode: Wenn True, werden chargedaten speziell behandelt
            ems_heartbeat_mode: Wenn True, wird die spezielle EMS-Heartbeat-Verarbeitung
            parallel_energy_stream_mode: Wenn True, wird die spezielle Verarbeitung
                für parallele Energie-Streams verwendet.

        Returns:
            Erweitertes sensors dict mit neuen Sensoren

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

        if not d:
            _LOGGER.debug(f"Configured report '{report_to_log}' not in response.")
            return sensors

        sens_select = self._get_sens_select(report)
        # Setze Report-Namen korrekt aus response
        report = key if key else report
        # Battery und Wallbox Handling
        if battery_mode or wallbox_mode:
            return self._handle_boxed_devices(
                d,
                sensors,
                report=report,
            )
        # EMS Heartbeat Mode
        if ems_heartbeat_mode:
            return self._handle_ems_heartbeat_mode(
                d, sensors, report, sens_select, suffix
            )
        # Parallel Energy Stream Mode
        if parallel_energy_stream_mode:
            return self._handle_parallel_energy_stream(
                d,
                sensors,
                report,
            )
        # Standardverarbeitung
        return self._handle_standard_mode(d, sensors, report, sens_select, suffix)

    def _handle_boxed_devices(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        *,
        report: str,
    ) -> dict[str, PowerOceanEndPoint]:
        for box_sn_raw, raw_payload in d.items():
            if box_sn_raw in ("", "updateTime"):
                continue

            payload = self._parse_battery_data(raw_payload)
            if not isinstance(payload, dict):
                continue

            detected = self._detect_box_schema(payload)

            if not detected:
                _LOGGER.debug("Unknown boxed device schema")
                continue

            # box_type wird aktuell nicht genutzt
            _, schema = detected

            device_sn = self._extract_box_sn(payload, schema, box_sn_raw)
            if not device_sn:
                continue

            device_info = self._get_device_info(
                sn=device_sn,
                name=f"{schema['name_prefix']} {device_sn}",
                model=schema["model"],
                via_sn=self.sn_inverter,
            )

            box_sensors = self._get_box_sensors(schema)

            paths = schema["paths"]

            for key in box_sensors:
                value = (
                    self._get_nested_value(payload, paths[key])
                    if paths and key in paths
                    else payload.get(key)
                )

                if value is None:
                    continue
                # Spezielles Handling für bestimmte base64 Keys
                if key in {"bpSn", "devSn"} and isinstance(value, str):
                    value = self._decode_sn(value)
                uid = f"{device_sn}_{report}_{key}"

                sensors[uid] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=uid,
                        serial=device_sn,
                        name=f"{device_sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=SensorMetaHelper.get_special_icon(key),
                        device_info=device_info,
                    )
                )
        return sensors

    def _handle_ems_heartbeat_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        # EMS Heartbeat: ggf. verschachtelte Strukturen, spezielle Behandlung
        for key, value in d.items():
            if key in sens_select:
                unique_id = f"{self.sn_inverter}_{report}_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=f"{self.sn_inverter}",
                        name=f"{self.sn_inverter}_{key}{suffix}",
                        friendly_name=f"{key}{suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=None,
                    )
                )
        # Besonderheiten Phasen
        phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
        if phases[1] in d:
            for _, phase in enumerate(phases):
                for key, value in d[phase].items():
                    name = f"{phase}_{key}"
                    unique_id = f"{self.sn_inverter}_{report}_{name}"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=f"{self.sn_inverter}",
                            name=f"{self.sn_inverter}_{name}{suffix}",
                            friendly_name=f"{name}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=None,
                        )
                    )
        # Besonderheit mpptPv
        if "mpptHeartBeat" in d:
            n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])
            for i in range(n_strings):
                for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                    unique_id = (
                        f"{self.sn_inverter}_{report}_mpptHeartBeat_mpptPv{i + 1}_{key}"
                    )
                    special_icon = None
                    if key.endswith("amp"):
                        special_icon = "mdi:current-dc"
                    elif key.endswith("pwr"):
                        special_icon = "mdi:solar-power"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=f"{self.sn_inverter}",
                            name=f"{self.sn_inverter}_mpptPv{i + 1}_{key}{suffix}",
                            friendly_name=f"mpptPv{i + 1}_{key}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=special_icon,
                        )
                    )
            # Gesamtleistung mpptPv
            total_power = sum(
                d["mpptHeartBeat"][0]["mpptPv"][i].get("pwr", 0)
                for i in range(n_strings)
            )
            unique_id = f"{self.sn_inverter}_{report}_mpptHeartBeat_mpptPv_pwrTotal"
            sensors[unique_id] = self._create_sensor(
                PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=f"{self.sn_inverter}",
                    name=f"{self.sn_inverter}_mpptPv_pwrTotal{suffix}",
                    friendly_name=f"mpptPv_pwrTotal{suffix}",
                    value=total_power,
                    unit="W",
                    description="Solarertrag aller Strings",
                    icon="mdi:solar-power",
                )
            )
        return sensors

    def _handle_parallel_energy_stream(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
    ) -> dict[str, PowerOceanEndPoint]:
        """Handle parallel energy stream data extraction."""
        para_list = d.get("paraEnergyStream", [])
        if not isinstance(para_list, list):
            _LOGGER.warning("paraEnergyStream is not a list")
            return sensors

        for device_data in para_list:
            dev_sn = device_data.get("devSn", "")
            _LOGGER.debug(f"Processing parallel dev_sn: {dev_sn}")

            # Suffix bestimmen: MASTER / SLAVE / ALL
            if dev_sn == self.sn:
                device_suffix = DeviceRole.MASTER.value
            elif dev_sn:
                device_suffix = DeviceRole.SLAVE.value
            else:
                device_suffix = DeviceRole.ALL.value

            model = device_data.get("model", "PowerOcean Inverter")
            device_info = self._get_device_info(
                sn=dev_sn,
                name=f"Inverter {dev_sn}",
                model=model,
                via_sn=self.sn_inverter,
            )

            for key, raw_value in device_data.items():
                if key == "devSn":
                    value = self._decode_sn(raw_value)
                    if not value:  # leer oder None
                        continue  # Sensor nicht anlegen
                else:
                    value = raw_value
                unique_id = f"{dev_sn}_{report}_paraEnergyStream_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=dev_sn or DeviceRole.ALL.value,
                        name=f"{dev_sn}_{key}{device_suffix}",
                        friendly_name=f"{key}{device_suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=SensorMetaHelper.get_special_icon(key),
                        device_info=device_info,
                    )
                )

        return sensors

    def _handle_standard_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        # Standardverarbeitung: einfache key-value Paare
        device_info = None  # fällt auf Inverter zurück
        report_string = f"_{report}"
        # spezielle Behandlung für 'data' Report
        if report == ReportMode.DEFAULT.value:
            report_string = ""
        device_sn = d.get("evSn", self.sn_inverter)
        if device_sn != self.sn_inverter:
            device_info = self._get_device_info(
                sn=device_sn,
                name=f"Charger {device_sn}",
                model="EcoFlow Charger",
                via_sn=self.sn_inverter,
            )

        for key, raw_value in d.items():
            if key in sens_select and not isinstance(raw_value, dict):
                # Spezielles Handling für bestimmte base64 Keys
                if key == "evSn":
                    value = self._decode_sn(raw_value)
                    if not value:  # leer oder None
                        continue  # Sensor nicht anlegen
                else:
                    value = raw_value
                unique_id = f"{device_sn}{report_string}_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=f"{device_sn}",
                        name=f"{device_sn}_{key}{suffix}",
                        friendly_name=f"{key}{suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=SensorMetaHelper.get_special_icon(key),
                        device_info=device_info,
                    )
                )
        return sensors

    def _decode_sn(self, value: str | None) -> str | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return base64.b64decode(value, validate=True).decode("utf-8").strip()
        except binascii.Error:
            _LOGGER.warning("Invalid base64 string for SN: %s", value)
            return value
        except UnicodeDecodeError:
            return value

    @staticmethod
    def flatten_json(y: Any) -> dict:
        """
        Flatten a nested JSON object into a flat dictionary.

        Args:
            y: The JSON object (dict or list) to flatten.

        Returns:
            dict: A flattened dictionary with keys representing the path to each value.

        """
        out = {}

        def flatten(x: Any, name: str = "") -> None:
            if isinstance(x, dict):
                for a in x:
                    flatten(x[a], f"{name}{a}_")
            elif isinstance(x, list):
                for i, a in enumerate(x):
                    flatten(a, f"{name}{i}_")
            else:
                out[name[:-1]] = x

        flatten(y)
        return out

    @staticmethod
    def _is_matching_report(key: str, report: str) -> bool:
        if not isinstance(key, str):
            return False

        # Spezialfall ENERGY_STREAM_REPORT
        if report == ReportMode.ENERGY_STREAM.value:
            return key.split("_", 1)[1] == report

        # Default-Fall
        return key.endswith(report)


class ApiResponseError(Exception):
    """Exception raised for API response errors."""


class ResponseTypeError(TypeError):
    """Exception raised when the response is not a dict."""

    def __init__(self, typename: str) -> None:
        """Initialize the exception with the provided type name."""
        super().__init__(f"Expected response to be a dict, got {typename}")


class AuthenticationFailedError(Exception):
    """Exception to indicate authentication failure."""


class FailedToExtractKeyError(Exception):
    """Exception raised when a required key cannot be extracted from a response."""

    def __init__(self, key: str, response: dict) -> None:
        """
        Initialize the exception with the missing key and response.

        Args:
            key (str): The key that could not be extracted.
            response (dict): The response dictionary where the key was missing.

        """
        self.key = key
        self.response = response
        super().__init__(f"Failed to extract key {key} from response: {response}")
