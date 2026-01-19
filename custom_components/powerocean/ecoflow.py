"""ecoflow.py: API for PowerOcean integration."""

import asyncio
import base64
import fnmatch
import re
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

from httpx import get
import orjson
import requests
import yaml
from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads
from jsonpath_ng.ext import parse as jsonpath_parse

from .const import (
    _LOGGER,
    ISSUE_URL_ERROR_MESSAGE,
    LENGTH_BATTERIE_SN,
    USE_MOCKED_RESPONSE,
    SENSOR_META,
    DEFAULT_META,
)


class ReportMode(Enum):
    """Enumeration for different report modes in the PowerOcean integration."""

    DEFAULT = "data"
    BATTERY = "BP_STA_REPORT"
    EMS = "EMS_HEARTBEAT"
    PARALLEL = "PARALLEL_ENERGY_STREAM_REPORT"
    EMS_CHANGE = "EMS_CHANGE_REPORT"
    WALLBOX = "EDEV_PARAM_REPOR"


class DeviceRole(str, Enum):
    """Enumeration for device roles in the PowerOcean integration."""

    MASTER = "_master"
    SLAVE = "_slave"
    ALL = "_all"
    EMPTY = ""  # Used when no specific role is assigned


# Mock path to response.json file
mocked_response = Path("documentation/response_87_user.json")


# Better storage of PowerOcean endpoint
class PowerOceanEndPoint(NamedTuple):
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

    """

    internal_unique_id: str
    serial: str
    name: str
    friendly_name: str
    value: object
    unit: str | None
    description: str
    icon: str | None


class SensorMetaHelper:
    """Helper class for sensor metadata such as units, descriptions, and icons."""

    @staticmethod
    def get_unit(key: str) -> str | None:
        """Get unit from key name using a dictionary mapping."""
        unit_mapping = {
            "pwr": "W",
            "power": "W",
            "amp": "A",
            "soc": "%",
            "soh": "%",
            "vol": "V",
            "watth": "Wh",
            "energy": "Wh",
            "percent": "%",
            "volume": "L",
            "temp": "°C",
        }

        # Check for direct matches using dictionary lookup
        for suffix, unit in unit_mapping.items():
            if key.lower().endswith(suffix):
                return unit

        # Special case for "Generation" in key
        if "Generation" in key:
            return "kWh"

        return None  # Default if no match found

    @staticmethod
    def get_description(key: str) -> str:
        """Get description from key name using a dictionary mapping."""
        # Dictionary for key-to-description mapping
        description_mapping = {
            "sysLoadPwr": "Hausnetz",
            "sysGridPwr": "Stromnetz",
            "mpptPwr": "Solarertrag",
            "bpPwr": "Batterieleistung",
            "bpSoc": "Ladezustand der Batterie",
            "online": "Online",
            "systemName": "System Name",
            "createTime": "Installations Datum",
            "bpVol": "Batteriespannung",
            "bpAmp": "Batteriestrom",
            "bpCycles": "Ladezyklen",
            "bpTemp": "Temperatur der Batteriezellen",
        }

        # Use .get() to avoid KeyErrors and return default value
        return description_mapping.get(key, key)  # Default to key if not found

    @staticmethod
    def get_special_icon(key: str) -> str | None:
        """Get special icon for a key."""
        # Dictionary für die Zuordnung von Keys zu Icons
        icon_mapping = {
            "mpptPwr": "mdi:solar-power",
            "online": "mdi:cloud-check",
            "sysGridPwr": "mdi:transmission-tower-import",
            "sysLoadPwr": "mdi:home-import-outline",
            "bpAmp": "mdi:current-dc",
        }

        # Standardwert setzen
        special_icon = icon_mapping.get(key)

        # Zusätzliche Prüfung für Keys, die mit "pv1" oder "pv2" beginnen
        if key.startswith(("pv1", "pv2", "pv3")):
            special_icon = "mdi:solar-power"

        return special_icon


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
        self.variants_dir = base_path / "variants"
        self.options = options  # Store Home Assistant instance
        self.sensor_config = None  # Initialisierung der Variable

    def get_device(self) -> dict:
        """Get device info."""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.27",  # Version vom Author.
            "build": "28",  # Version vom Author.
            "name": "PowerOcean",
            "features": "Photovoltaik",
        }

        return self.device

    async def load_sensor_config(self):
        # Dies ist die Methode, die den asynchronen Aufruf macht
        self.sensor_config = await self._load_sensor_config()
        # Du kannst weitere Initialisierungen vornehmen, nachdem die Konfiguration geladen wurde
        _LOGGER.debug("Sensor configuration loaded: %s", self.sensor_config)

    async def _load_sensor_config(self):
        config_path = Path(self.variants_dir) / "sensors_83.yaml"
        content = await asyncio.to_thread(self._read_file, config_path)
        # return yaml.safe_load(content)

        # Lade den YAML-Inhalt
        config = yaml.safe_load(content)

        # Überprüfe, ob die Konfiguration ein Dictionary ist und die 'reports'-Liste existiert
        if isinstance(config, dict) and "reports" in config:
            reports = config["reports"]

            # Überprüfe, ob 'reports' eine Liste von Dictionaries ist
            if isinstance(reports, list) and all(
                isinstance(item, dict) for item in reports
            ):
                # Hier kannst du durch die Reports iterieren und jeden Report konfigurieren
                for report_cfg in reports:
                    report = report_cfg.get("report")
                    if not report:
                        _LOGGER.warning(
                            f"Report fehlt in der Konfiguration: {report_cfg}"
                        )
                        continue  # Überspringe, wenn der Reportname fehlt

                    # Überprüfe, ob 'sensors' im Report definiert ist
                    sensors = report_cfg.get("sensors", [])
                    if isinstance(sensors, list) and all(
                        isinstance(sensor, dict) for sensor in sensors
                    ):
                        # Hier kannst du die sensor Konfiguration weiterverarbeiten
                        _LOGGER.debug(f"Verarbeite Sensoren für Report: {report}")

                    else:
                        _LOGGER.error(
                            f"Ungültiges Format in der 'sensors'-Liste des Reports '{report}'. Erwartet wurde eine Liste von Dictionaries."
                        )
                        return []  # Rückgabe einer leeren Liste im Fehlerfall
            else:
                _LOGGER.error(
                    f"Ungültiges Format in der 'reports'-Liste. Erwartet wurde eine Liste von Dictionaries, aber erhalten: {type(reports)}."
                )
                return []  # Rückgabe einer leeren Liste im Fehlerfall
        else:
            _LOGGER.error(
                f"Ungültiges Format in der Konfiguration. Erwartet wurde ein Dictionary mit einer 'reports'-Liste, aber erhalten: {type(config)}."
            )
            return []  # Rückgabe einer leeren Liste im Fehlerfall

        # Wenn keine Fehler aufgetreten sind, gib die vollständige 'reports'-Liste zurück
        return reports  # Hier gibt es jetzt ein explizites return für den Erfolg

    def _read_file(self, config_path):
        with config_path.open("r", encoding="utf-8") as f:
            return f.read()

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
                try:
                    with Path.open(mocked_response, "r", encoding="utf-8") as file:
                        response = json_loads(file.read())
                except FileNotFoundError:
                    _LOGGER.debug(
                        f"Mocked response file not present: {mocked_response}"
                    )
            # Log the response for debugging or development purposes
            # _LOGGER.debug(f"{response}")
            flattened_data = Ecoflow.flatten_json(response.get("data", {}))
            _LOGGER.debug("Available keys: %s", flattened_data.keys())
            # _LOGGER.debug(
            #     "Flattened data keys (%d): %s",
            #     len(flattened_data),
            #     list(flattened_data.keys()),
            # )

            if isinstance(response, dict):
                return self._get_sensors(
                    response=response,
                    flattened_data=flattened_data,
                )
            raise ResponseTypeError(type(response).__name__)

        except ConnectionError as err:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error) from err

    def __read_json_file(self) -> dict:
        try:
            with self.datapointfile.open("r", encoding="utf-8") as file:
                data = json_loads(file.read())
                if isinstance(data, dict):
                    return data
                _LOGGER.error(
                    f"JSON content is not a dict in file: {self.datapointfile}"
                )
                return {}
        except FileNotFoundError:
            _LOGGER.error(f"File not found: {self.datapointfile}")
        except orjson.JSONDecodeError:
            _LOGGER.error(f"Error decoding JSON in file: {self.datapointfile}")
        return {}

    def _get_reports(self) -> list:
        """Retrieve report selection from JSON file."""
        reports = self.__read_json_file()
        if isinstance(reports, dict):
            return list(reports.keys())
        return []

    def _get_sens_select(self, report: str) -> list:
        """Retrieve sensor selection from JSON file."""
        datapoints = self.__read_json_file()
        if isinstance(datapoints, dict):
            value = datapoints.get(report, [])
        if isinstance(value, list):
            return value
        # If value is not a list, return an empty list
        return []

    def _create_sensor(self, endpoint: PowerOceanEndPoint) -> PowerOceanEndPoint:
        return endpoint

    @staticmethod
    def flatten_json(
        data: dict[str, Any],
        parent_key: str = "",
        sep: str = ".",
    ) -> dict[str, Any]:
        items = {}

        for key, value in data.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else key

            if isinstance(value, str) and value.strip().startswith("{"):
                try:
                    value = json_loads(value)
                except orjson.JSONDecodeError:
                    pass

            if isinstance(value, dict):
                items.update(Ecoflow.flatten_json(value, new_key, sep))
            else:
                items[new_key] = value

        return items

    def deserialize_json_strings(self, data):
        """Rekursiv prüfen und deserialisieren von JSON-Strings."""
        if isinstance(data, dict):
            # Gehe rekursiv durch das Wörterbuch
            for key, value in data.items():
                data[key] = self.deserialize_json_strings(value)
        elif isinstance(data, list):
            # Gehe rekursiv durch die Liste
            for idx, value in enumerate(data):
                data[idx] = self.deserialize_json_strings(value)
        elif isinstance(data, str):
            # Prüfe, ob der String ein gültiger JSON-String ist
            try:
                # Versuche den String zu deserialisieren
                return json_loads(data)
            except orjson.JSONDecodeError:
                # Falls es kein gültiger JSON-String ist, gebe den Originalwert zurück
                return data
        return data

    def process_data(data):
        # Deserialisieren von möglichen JSON-Strings in den Daten
        data = json_loads.deserialize_json_strings(data)
        return data

    def _get_sensors(
        self,
        response: dict,
        flattened_data: dict[str, Any] | None = None,
    ) -> dict[str, PowerOceanEndPoint]:
        sensors: dict[str, PowerOceanEndPoint] = {}  # start with empty dict
        self.sn_inverter = self.sn
        # error handling for response
        data = self.deserialize_json_strings(response.get("data"))
        # data = response.get("data")
        if not isinstance(data, dict):
            _LOGGER.error("No 'data' in response.")
            return sensors  # return empty dict if no data

        sensors = self._extract_sensors_from_jsonpath(
            data,
            sensors,
        )
        # Handle generic 'data' report
        reports_data = self._get_reports()
        reports = []
        report = ReportMode.DEFAULT.value
        if report in reports_data:
            # Special case for 'data' report
            # sensors.update(
            #     self._extract_sensors_from_report_org(
            #         response,
            #         sensors,
            #         report,
            #     )
            # )
            reports = [x for x in reports_data if x != "data"]
        # _LOGGER.debug(f"Reports to look for: {reports}")

        # # Dual inverter installation
        # if "parallel" in data:
        #     response_parallel = data["parallel"]
        #     inverters = list(response_parallel.keys())

        #     for element in inverters or [self.sn]:
        #         self.sn_inverter = element
        #         response_base = response_parallel.get(element, {})
        #         _LOGGER.debug(f"Processing inverter: {element}")
        #         suffix = (
        #             DeviceRole.MASTER.value
        #             if element == self.sn
        #             else DeviceRole.SLAVE.value
        #         )
        #         for report in reports:
        #             # Besonderheit: JTS1_ENERGY_STREAM_REPORT  # noqa: ERA001
        #             if "ENERGY_STREAM_REPORT" in report:
        #                 report_key = re.sub(r"JTS1_", "JTS1_PARALLEL_", report)
        #             else:
        #                 report_key = report

        #             # sensors.update(
        #             #     self._extract_sensors_from_report_org(
        #             #         response_base,
        #             #         sensors,
        #             #         report_key,
        #             #         suffix=suffix,
        #             #         battery_mode=ReportMode.BATTERY.value in report_key,
        #             #         ems_heartbeat_mode=ReportMode.EMS.value in report_key,
        #             #         parallel_energy_stream_mode=ReportMode.PARALLEL.value
        #             #         in report_key,
        #             #     )
        #             # )
        # # Single inverter installation
        # elif "quota" in data:
        #     response_base = data["quota"]
        #     for report in reports:
        #         sensors.update(
        #             self._extract_sensors_from_report_org(
        #                 response_base,
        #                 sensors,
        #                 report,
        #                 battery_mode=ReportMode.BATTERY.value in report,
        #                 ems_heartbeat_mode=ReportMode.EMS.value in report,
        #             )
        #         )
        # else:
        #     _LOGGER.warning(
        #         "Neither 'quota' nor 'parallel' inverter data found in response."
        #     )
        return sensors

    def _extract_sensors_from_flat_data(
        self,
        flat_data: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
    ) -> dict[str, PowerOceanEndPoint]:
        for cfg in self.sensor_config:
            path = cfg["path"]

            for full_key, value in flat_data.items():
                if not fnmatch.fnmatch(full_key, path):
                    continue

                suffix = ""
                if cfg.get("per_instance"):
                    instance = full_key.split(".")[-2]
                    suffix = f"_{instance}"

                unique_id = f"{self.sn_inverter}_{cfg['id']}{suffix}"

                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn_inverter,
                        name=f"{cfg['id']}{suffix}",
                        friendly_name=f"{cfg['id'].replace('_', ' ').title()}{suffix}",
                        value=value,
                        unit=cfg.get("unit"),
                        icon=cfg.get("icon"),
                        device_class=cfg.get("device_class"),
                        description=cfg.get("description"),
                    )
                )

        return sensors

    def _extract_sensors_from_jsonpath_org(
        self,
        data: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
    ) -> dict[str, PowerOceanEndPoint]:
        for cfg in self.sensor_config:
            _LOGGER.debug(f"Processing sensor config: {cfg}")
            expr = jsonpath_parse(cfg["jsonpath"])
            matches = expr.find(data)

            for match in matches:
                value = match.value
                _LOGGER.debug(
                    f"Found value: {value} for key: {cfg['id']}"
                )  # Logging hinzufügen
                suffix = ""
                if cfg.get("per_instance"):
                    # Batterie-ID aus Pfad extrahieren
                    path_str = str(match.full_path)
                    instance = path_str.split(".")[-2]
                    suffix = f"_{instance}"

                    # Finde die Instanzen und speichere sie in einer Liste
                    all_instances = [m.value for m in expr.find(data)]
                    all_instances.reverse()  # Umkehren der Reihenfolge der gefundenen Instanzen

                    # Jetzt die Instanz als bpackX darstellen, basierend auf der umgekehrten Reihenfolge
                    for idx, inst in enumerate(all_instances):
                        if (
                            inst == match.value
                        ):  # Identifiziere, welches Element das aktuelle ist
                            suffix = f"_{len(all_instances) - idx}"

                if cfg.get("unique_id_template"):
                    # Template für die unique_id erstellen
                    unique_id = cfg["unique_id_template"].format(
                        sn_inverter=self.sn_inverter, pack=instance, id=cfg["id"]
                    )
                else:
                    unique_id = f"{self.sn_inverter}_{cfg['id']}{suffix}"

                if unique_id in sensors:
                    continue

                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn_inverter,
                        name=f"{cfg['id']}{suffix}",
                        friendly_name=f"{cfg['id'].replace('_', ' ')}{suffix}",
                        value=value,
                        unit=cfg.get("unit"),
                        icon=cfg.get("icon"),
                        # device_class=cfg.get("device_class"),
                        description=cfg.get("description"),
                    )
                )

        return sensors

    def _extract_sensors_from_jsonpath_best(
        self,
        data: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
    ) -> dict[str, PowerOceanEndPoint]:
        """
        Extrahiere Sensoren anhand von JSONPath-Konfiguration und SENSOR_META.
        Unterstützt wiederholte Instanzen (per_instance) z.B. Batterie-Packs.
        """

        for cfg in self.sensor_config:
            expr = jsonpath_parse(cfg["jsonpath"])
            matches = expr.find(data)

            # Bei mehrfachen Instanzen die Reihenfolge umdrehen (erste → letzte bpack)
            if cfg.get("per_instance") and matches:
                matches = list(reversed(matches))

            for idx, match in enumerate(matches):
                value = match.value
                suffix = ""

                if cfg.get("per_instance"):
                    # suffix = _bpack1, _bpack2 … basierend auf der Reihenfolge der Matches
                    suffix = f"_bpack{idx + 1}"

                unique_id = f"{self.sn_inverter}_{cfg['id']}{suffix}"
                if unique_id in sensors:
                    continue

                # Metadaten automatisch aus SENSOR_META
                meta = SENSOR_META.get(cfg["id"], {})
                unit = meta.get("unit")
                device_class = meta.get("device_class")
                state_class = meta.get("state_class")
                icon = meta.get("icon")
                description = meta.get("description")

                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn_inverter,
                        name=f"{cfg['id']}{suffix}",
                        friendly_name=f"{cfg['id'].replace('_', ' ')}{suffix}",
                        value=value,
                        unit=unit,
                        icon=icon,
                        description=description,
                        # device_class=device_class,  # falls PowerOceanEndPoint unterstützt
                        # state_class=state_class,    # falls PowerOceanEndPoint unterstützt
                    )
                )

        return sensors

    def _build_jsonpath(self, report: str, field: str) -> str:
        base = "$.data" if report == "" else f"$.quota.{report}"
        return f"{base}.{field}"

    def _meta_key(self, field: str) -> str:
        return field.split(".")[-1]

    def _build_names(
        self,
        report: str,
        field: str,
        label: str | None,
        suffix: str,
    ) -> tuple[str, str]:
        short = field.replace(".", "_")
        name = f"{report}_{short}{suffix}"
        friendly = label if label else short.replace("_", " ").title() + suffix
        return name, friendly

    def _extract_sensors_from_jsonpath(
        self,
        data: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
    ) -> dict[str, PowerOceanEndPoint]:
        for report_cfg in self.sensor_config:
            # _LOGGER.debug(f"Processing report config: {report_cfg}")
            report = report_cfg["report"]

            if report_cfg.get("report") != report:
                continue
            base = "$" if report == "data" else f"$.quota.{report}"

            # Überprüfe, ob für diesen Report die 'sensors' definiert sind
            if "sensors" in report_cfg:
                # Iteriere über die Sensoren dieses Reports
                for s in report_cfg["sensors"]:
                    field = s["field"]
                    label = s.get("label")
                    per_instance = s.get("per_instance", False)
                    instance_name = s.get("instance_name", "bpack")
                    aggregate = s.get("aggregate")
                    if not field:
                        _LOGGER.warning(
                            f"Kein 'field' in Sensor-Konfiguration für Report '{report}'."
                        )
                        continue

                    _LOGGER.debug(f"Field: {field}")

                    # Behandle die Verschachtelung von Feldern wie "mpptHeartBeat[0].mpptPv[0].vol"
                    expr_str = f"{base}.{field}"

                    # Extrahiere Indizes und baue den JSONPath für verschachtelte Felder
                    while "[" in expr_str and "]" in expr_str:
                        # Suche nach dem ersten Array-Zugriff
                        prefix, suffix = expr_str.split("[", 1)
                        index = int(suffix.split("]")[0])  # Extrahiere den Index
                        expr_str = (
                            f"{prefix}[{index}].{suffix.split(']')[1]}"
                            if "]" in suffix
                            else f"{prefix}[{index}]"
                        )

                    # Erstelle den finalen JSONPath
                    expr = jsonpath_parse(expr_str)
                    _LOGGER.debug(f"Using jsonpath: {expr_str}")

                    matches = expr.find(data)

                    if not matches:
                        _LOGGER.warning(
                            f"Keine Übereinstimmung für JSONPath '{expr_str}' im Report '{report}'."
                        )
                        continue

                    # Aggregation (z. B. mpptPv total)
                    if aggregate == "sum":
                        value = sum(m.value or 0 for m in matches)
                        matches = [value]

                    # Reihenfolge drehen (Batterie)
                    if per_instance:
                        matches = list(reversed(matches))

                    for idx, match in enumerate(matches):
                        value = match if aggregate else match.value

                        suffix = ""
                        if per_instance:
                            suffix = f"_{instance_name}{idx + 1}"

                        field_key = field.replace(".", "_").replace("[*]", "")
                        unique_id = f"{self.sn_inverter}_{report}_{field_key}{suffix}"

                        if unique_id in sensors:
                            continue

                        # Hole Meta-Daten für das Feld
                        meta_key = field.split(".")[-1]
                        meta = SENSOR_META.get(meta_key, {})

                        name = f"{self.sn_inverter}_{field_key}{suffix}"
                        friendly = label or f"{field_key}{suffix}"

                        # Sensor erstellen
                        sensors[unique_id] = self._create_sensor(
                            PowerOceanEndPoint(
                                internal_unique_id=unique_id,
                                serial=self.sn_inverter,
                                name=name,
                                friendly_name=friendly,
                                value=value,
                                unit=meta.get("unit"),
                                icon=meta.get("icon"),
                                description=meta.get("description"),
                            )
                        )
            else:
                _LOGGER.warning(f"Der Report '{report}' enthält keine Sensoren.")

        return sensors

    def _extract_sensors_from_report(
        self,
        response: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
    ) -> dict[str, PowerOceanEndPoint]:
        data = response.get("data", {})
        return self._extract_sensors_from_jsonpath(data, sensors)

    def _extract_sensors_from_report_org(  # noqa: PLR0913
        self,
        response: dict[str, Any],
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        suffix: str = "",
        *,
        battery_mode: bool = False,
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
            ems_heartbeat_mode: Wenn True, wird die spezielle EMS-Heartbeat-Verarbeitung
            parallel_energy_stream_mode: Wenn True, wird die spezielle Verarbeitung
                für parallele Energie-Streams verwendet.

        Returns:
            Erweitertes sensors dict mit neuen Sensoren

        """
        # Report-Key ggf. anpassen
        if report not in response:
            report_to_log = report
            report = re.sub(r"JTS1_", "RE307_", report)

        d = response.get(report)
        if not d:
            _LOGGER.debug(f"Configured report '{report_to_log}' not in response.")
            return sensors

        sens_select = self._get_sens_select(report)
        if battery_mode:
            return self._handle_battery_mode(d, sensors, report, sens_select, suffix)
        # EMS Heartbeat Mode
        if ems_heartbeat_mode:
            return self._handle_ems_heartbeat_mode(
                d, sensors, report, sens_select, suffix
            )
        # Parallel Energy Stream Mode
        if parallel_energy_stream_mode:
            return self._handle_parallel_energy_stream(
                d, sensors, report, sens_select, suffix
            )
        # Standardverarbeitung
        return self._handle_standard_mode(d, sensors, report, sens_select, suffix)

    def _handle_battery_mode(
        self,
        d: dict,
        sensors: dict[str, PowerOceanEndPoint],
        report: str,
        sens_select: list,
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        """Handle battery mode data extraction."""
        # Batteriedaten: d enthält JSON Strings pro Batterie
        keys = list(d.keys())
        batts = [s for s in keys if len(s) > LENGTH_BATTERIE_SN]
        prefix = "bpack"
        for ibat, bat in enumerate(reversed(batts)):
            name = f"{prefix}{ibat + 1}_"
            raw_data = d.get(bat)
            d_bat = self._parse_battery_data(raw_data)
            if isinstance(d_bat, dict):
                for key, value in d_bat.items():
                    if key in sens_select:
                        unique_id = f"{self.sn_inverter}_{report}_{bat}_{key}"
                        sensors[unique_id] = self._create_sensor(
                            PowerOceanEndPoint(
                                internal_unique_id=unique_id,
                                serial=f"{self.sn_inverter}",
                                name=f"{self.sn_inverter}_{name}{key}{suffix}",
                                friendly_name=f"{name}{key}{suffix}",
                                value=value,
                                unit=SensorMetaHelper.get_unit(key),
                                description=f"{name}{SensorMetaHelper.get_description(key)}",
                                icon=SensorMetaHelper.get_special_icon(key),
                            )
                        )
            else:
                _LOGGER.error(f"Battery data for '{bat}' is not a dict: {type(d_bat)}")
        return sensors

    def _parse_battery_data(self, raw_data: dict | str | None) -> dict | None:
        if isinstance(raw_data, str):
            try:
                json_loads(raw_data)
            except orjson.JSONDecodeError as e:
                print(raw_data[e.pos - 50 : e.pos + 50])
                raise
            data = json_loads(raw_data)
            if isinstance(data, dict):
                return data
            _LOGGER.error(f"Parsed battery data is not a dict: {type(data)}")
            return None
        if isinstance(raw_data, dict):
            return raw_data
        _LOGGER.error(f"Unexpected battery data type: {type(raw_data)}")
        return None

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
        sens_select: list,  # noqa: ARG002
        suffix: str = "",
    ) -> dict[str, PowerOceanEndPoint]:
        """Handle parallel energy stream data extraction."""
        if "paraEnergyStream" in d:
            para_list = d.get("paraEnergyStream", [])
            for device_data in para_list:
                dev_sn = device_data.get("devSn")
                if not dev_sn or len(dev_sn) < LENGTH_BATTERIE_SN:
                    dev_sn = ""  # Fallback für unbekannte Seriennummer
                _LOGGER.debug(f"Processing parallel dev_sn: {dev_sn}")
                if dev_sn == self.sn:
                    suffix = DeviceRole.MASTER.value
                elif dev_sn != "":
                    suffix = DeviceRole.SLAVE.value
                else:
                    suffix = DeviceRole.ALL.value

                for key, value in device_data.items():
                    unique_id = f"{dev_sn}_{report}_paraEnergyStream_{key}"
                    sensors[unique_id] = self._create_sensor(
                        PowerOceanEndPoint(
                            internal_unique_id=unique_id,
                            serial=dev_sn,
                            name=f"{dev_sn}_{key}{suffix}",
                            friendly_name=f"{key}{suffix}",
                            value=value,
                            unit=SensorMetaHelper.get_unit(key),
                            description=SensorMetaHelper.get_description(key),
                            icon=SensorMetaHelper.get_special_icon(key),
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
        report_string = f"_{report}"
        # spezielle Behandlung für 'data' Report
        if report == ReportMode.DEFAULT.value:
            report_string = ""
        for key, value in d.items():
            if key in sens_select and not isinstance(value, dict):
                unique_id = f"{self.sn_inverter}{report_string}_{key}"
                sensors[unique_id] = self._create_sensor(
                    PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=f"{self.sn_inverter}",
                        name=f"{self.sn_inverter}_{key}{suffix}",
                        friendly_name=f"{key}{suffix}",
                        value=value,
                        unit=SensorMetaHelper.get_unit(key),
                        description=SensorMetaHelper.get_description(key),
                        icon=SensorMetaHelper.get_special_icon(key),
                    )
                )
        return sensors


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
