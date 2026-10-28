"""
coordinator.py: Home Assistant DataUpdateCoordinator for PowerOcean.

This module defines `PowerOceanCoordinator`, a subclass of
`DataUpdateCoordinator` that periodically fetches live data from
EcoFlow devices using `HAEcoflowApi` and parses them with `parse()`.

It handles:
- Login during the first refresh (`_async_setup`)
- Automatic periodic updates
- A new login when the EcoFlow cloud rejects the current token
- Mapping API errors to `UpdateFailed` / `ConfigEntryAuthFailed`
- Building the endpoints, extended by datapoints that appear later

Usage:
  coordinator = PowerOceanCoordinator(hass, entry, api)
  await coordinator.async_config_entry_first_refresh()
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, EcoflowApiError, EcoflowConnectionError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER
from .ecoflow import HAEcoflowApi
from .mqtt import (
    MODULE_EMS,
    MODULE_METER,
    ModuleVersion,
    MqttCallbacks,
    PowerOceanMqtt,
    new_seq,
)
from .parse import parse
from .types import PowerOceanEndPoint

if TYPE_CHECKING:
    from .errors import ActiveError

type PowerOceanConfigEntry = ConfigEntry[PowerOceanCoordinator]

# How often the module firmware is asked for again (it also comes after
# every reconnect, e.g. after a firmware update)
MQTT_REFRESH_INTERVAL = timedelta(hours=6)
# How long a command may wait for the acknowledgement of the system
COMMAND_TIMEOUT = 10
# Datapoint with the LED brightness (sensor emsCtrlLedBright of the inverter)
LED_BRIGHTNESS_SUFFIX = "_EMS_CHANGE_REPORT_emsCtrlLedBright"

# Parser errors caused by unexpected API payloads
PARSE_ERRORS = (KeyError, IndexError, TypeError, ValueError, AttributeError)


class PowerOceanCoordinator(DataUpdateCoordinator[dict[str, float | int | str]]):
    """Coordinate periodic fetching and parsing of PowerOcean sensor values."""

    config_entry: PowerOceanConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: PowerOceanConfigEntry,
        api: HAEcoflowApi,
    ) -> None:
        """Initialize the PowerOcean data update coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="PowerOcean",
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
        )
        self.api = api
        # Grows with every new datapoint, read by the platforms
        self.endpoints: dict[str, PowerOceanEndPoint] = {}
        # Last raw API response, for diagnostics
        self.last_response: dict[str, Any] | None = None
        # Active errors, by unique_id of the error count sensor
        self.active_errors: dict[str, list[ActiveError]] = {}
        # Firmware version, by serial number of the device
        self.firmware: dict[str, str] = {}
        self._http_firmware: dict[str, str] = {}
        # Optional MQTT connection (option "mqtt"): firmware of the modules
        self.mqtt: PowerOceanMqtt | None = None
        self.modules: dict[int, ModuleVersion] = {}
        self._mqtt_firmware: dict[str, str] = {}
        self._mqtt_refresh_unsub: Callable[[], None] | None = None
        # Commands waiting for their acknowledgement, by seq
        self._pending_acks: dict[int, asyncio.Future[None]] = {}

    async def _async_setup(self) -> None:
        """Log in once before the first refresh."""
        await self._async_authorize()

    async def _async_update_data(self) -> dict[str, float | int | str]:
        """Fetch and parse the live data, adding endpoints for new datapoints."""
        response = await self._async_fetch()
        self.last_response = response

        try:
            result = parse(response, self.api.sn, self.api.variant)
            # A device that was offline at startup brings its datapoints later
            for unique_id, datapoint in result.first().items():
                if unique_id not in self.endpoints:
                    self.endpoints[unique_id] = PowerOceanEndPoint.from_datapoint(
                        datapoint, result.firmware.get(datapoint.device.sn)
                    )
        except PARSE_ERRORS as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": repr(err)},
            ) from err

        self.active_errors = result.active_errors
        self._update_firmware(result.firmware)
        return result.values()

    def _update_firmware(self, firmware: dict[str, str]) -> None:
        """Show a changed firmware version on the device page."""
        self._http_firmware = firmware
        self.firmware = {**firmware, **self._mqtt_firmware}
        # Devices not registered yet get the version from their entities
        device_registry = dr.async_get(self.hass)
        for sn, version in self.firmware.items():
            device = device_registry.async_get_device(identifiers={(DOMAIN, sn)})
            if device and device.sw_version != version:
                device_registry.async_update_device(device.id, sw_version=version)

    async def _async_fetch(self) -> dict[str, Any]:
        """Fetch data, logging in again once if the request was rejected."""
        try:
            return await self.api.fetch_raw()
        except EcoflowConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except EcoflowApiError as err:
            # Typically an expired token, reported as HTTP 401/403 or as an
            # error code in an HTTP 200 response.
            LOGGER.debug("Request rejected (%s), logging in again", err)

        await self._async_authorize()

        try:
            return await self.api.fetch_raw()
        except EcoflowApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _async_authorize(self) -> None:
        """Log in and map errors to the matching Home Assistant behavior."""
        try:
            await self.api.async_authorize()
        except AuthenticationError as err:
            # Starts the reauth flow
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except EcoflowApiError as err:
            # Retried with the next update (or setup retry during first refresh)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    # -----------------------------
    # Optional MQTT connection
    # -----------------------------

    async def async_start_mqtt(self) -> None:
        """Connect via MQTT to read the module firmware; failures only log."""
        if getattr(self.api, "mock_response", None) is not None:
            LOGGER.debug("Mocked response: no MQTT connection")
            return
        if not self.api.user_id:
            LOGGER.warning("MQTT: no user ID from the login, not connecting")
            return
        try:
            credentials = await self.api.async_mqtt_credentials()
            callbacks = MqttCallbacks(
                on_modules=self._from_mqtt_thread(self._apply_modules),
                on_led_brightness=self._from_mqtt_thread(self._apply_led_brightness),
                on_ack=self._from_mqtt_thread(self._apply_ack),
            )
            client = PowerOceanMqtt(
                credentials, self.api.sn, self.api.user_id, callbacks
            )
            await self.hass.async_add_executor_job(client.start)
        except (EcoflowApiError, OSError, ValueError, KeyError) as err:
            LOGGER.warning("MQTT not started, no module firmware: %s", err)
            return
        self.mqtt = client
        self._mqtt_refresh_unsub = async_track_time_interval(
            self.hass, self._async_request_full_state, MQTT_REFRESH_INTERVAL
        )
        # Settings (LED brightness) become available
        self.async_update_listeners()

    async def async_stop_mqtt(self) -> None:
        """Close the MQTT connection, if any."""
        if self._mqtt_refresh_unsub:
            self._mqtt_refresh_unsub()
            self._mqtt_refresh_unsub = None
        if self.mqtt:
            client, self.mqtt = self.mqtt, None
            await self.hass.async_add_executor_job(client.stop)

    async def _async_request_full_state(self, _now: datetime) -> None:
        """Ask the system for its full state again (module firmware)."""
        if self.mqtt:
            await self.hass.async_add_executor_job(self.mqtt.request_full_state)

    def _from_mqtt_thread[T](self, apply: Callable[[T], None]) -> Callable[[T], None]:
        """Return a callback that hands a value over from paho's thread."""
        return lambda value: self.hass.loop.call_soon_threadsafe(apply, value)

    @callback
    def _apply_modules(self, modules: dict[int, ModuleVersion]) -> None:
        """Show the firmware of the EMS and the smart meter on their devices."""
        if not self.mqtt:
            # Message arrived while the connection was being closed
            return
        self.modules = modules
        inverter, meter_device = self._mqtt_firmware_devices
        firmware: dict[str, str] = {}
        if ems := modules.get(MODULE_EMS):
            firmware[inverter] = ems.firmware
        if meter := modules.get(MODULE_METER):
            firmware[meter_device] = meter.firmware
        self._mqtt_firmware = firmware
        self._update_firmware(self._http_firmware)

    @property
    def _mqtt_firmware_devices(self) -> tuple[str, str]:
        """Devices whose firmware only comes from MQTT: inverter, smart meter."""
        # Device of the smart meter, see parse._read_meters
        return self.api.sn, f"{self.api.sn}_meter{MODULE_METER}"

    @callback
    def clear_mqtt_firmware(self) -> None:
        """Remove the firmware a former MQTT connection showed (option off)."""
        device_registry = dr.async_get(self.hass)
        for sn in self._mqtt_firmware_devices:
            if sn in self.firmware:
                continue
            device = device_registry.async_get_device(identifiers={(DOMAIN, sn)})
            if device and device.sw_version:
                device_registry.async_update_device(device.id, sw_version=None)

    # -----------------------------
    # LED brightness
    # -----------------------------

    @property
    def led_brightness_key(self) -> str | None:
        """Return the datapoint with the LED brightness of the inverter."""
        prefix = f"{self.api.sn}_"
        return next(
            (
                unique_id
                for unique_id in self.data or {}
                if unique_id.startswith(prefix)
                and unique_id.endswith(LED_BRIGHTNESS_SUFFIX)
            ),
            None,
        )

    async def async_set_led_brightness(self, percent: int) -> None:
        """
        Set the LED brightness via MQTT and wait for the acknowledgement.

        Raises:
            HomeAssistantError: Without MQTT connection, or if the system does
                not acknowledge the command in time.

        """
        if not self.mqtt:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mqtt_not_connected"
            )
        seq = new_seq()
        acknowledged = self.hass.loop.create_future()
        self._pending_acks[seq] = acknowledged
        try:
            await self.hass.async_add_executor_job(
                self.mqtt.set_led_brightness, percent, seq
            )
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await acknowledged
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_acknowledged"
            ) from err
        finally:
            self._pending_acks.pop(seq, None)
        # Acknowledged: show the value before the system reports it
        self._apply_led_brightness(percent)

    @callback
    def _apply_ack(self, seq: int) -> None:
        """Complete the command the system acknowledged."""
        acknowledged = self._pending_acks.get(seq)
        if acknowledged and not acknowledged.done():
            acknowledged.set_result(None)

    @callback
    def _apply_led_brightness(self, percent: int) -> None:
        """Show a new LED brightness (sensor and number) without polling."""
        key = self.led_brightness_key
        if not self.mqtt or key is None or self.data.get(key) == percent:
            return
        self.data = {**self.data, key: percent}
        self.async_update_listeners()
