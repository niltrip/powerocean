"""config_flow.py: Config flow for PowerOcean integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, IntegrationError
from homeassistant.helpers.selector import selector

from .const import DEFAULT_NAME, DEFAULT_SCAN_INTERVAL, DOMAIN, ISSUE_URL_ERROR_MESSAGE
from .ecoflow import Ecoflow

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)

# Schema for the user step and device options step
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID, default=""): str,
        vol.Required(CONF_EMAIL, default=""): str,
        vol.Required(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_MODEL_ID, default="83"): selector(
            {
                "select": {
                    "options": [
                        {"label": "PowerOcean", "value": "83"},
                        {"label": "PowerOcean DC Fit", "value": "85"},
                        {"label": "PowerOcean Single Phase", "value": "86"},
                        {"label": "PowerOcean Plus", "value": "87"},
                    ],
                    "mode": "dropdown",
                }
            }
        ),
    }
)

STEP_DEVICE_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FRIENDLY_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): selector(
            {
                "number": {
                    "min": 10,
                    "max": 60,
                    "unit_of_measurement": "s",
                    "mode": "box",
                }
            }
        ),
    }
)

_LOGGER = logging.getLogger(__name__)


async def validate_input_for_device(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    ecoflow = Ecoflow(
        data[CONF_DEVICE_ID],
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
        data[CONF_MODEL_ID],
        options={},
    )

    try:
        # Check for authentication
        await hass.async_add_executor_job(ecoflow.authorize)
        # Get device info
        return await hass.async_add_executor_job(ecoflow.get_device)

    except IntegrationError as e:
        _LOGGER.exception(
            "Failed to connect to PowerOcean device: %s%s",
            e,
            ISSUE_URL_ERROR_MESSAGE,
        )
        raise HomeAssistantError from e  # Verwenden der Standardfehlerklasse

    except Exception as e:  # allgemeine Fehlerbehandlung
        _LOGGER.exception(
            "Authentication failed: %s%s",
            e,
            ISSUE_URL_ERROR_MESSAGE,
        )
        raise HomeAssistantError from e


class PowerOceanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PowerOcean."""

    VERSION = 1.3

    def __init__(self) -> None:
        """Instanzvariablen für den Flow-Verlauf."""
        self._cloud_data: dict[str, Any] = {}
        self._device_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                # 1. Validierung
                device = await validate_input_for_device(self.hass, user_input)

                # 2. Unique ID prüfen
                unique_id = f"PowerOcean_{device['serial']}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                # 3. Daten für Schritt 2 zwischenspeichern
                self._cloud_data = user_input
                self._device_info = device

                # 4. Weiter zu Schritt 2 (kein return async_create_entry!)
                return await self.async_step_device_options()
            except HomeAssistantError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 2: Zusätzliche Optionen abfragen."""
        errors = {}
        if user_input is not None:
            friendly_name = sanitize_device_name(
                user_input[CONF_FRIENDLY_NAME],
                fall_back=DEFAULT_NAME,
            )
            return self.async_create_entry(
                title=friendly_name,
                data=self._cloud_data,
                options={
                    **user_input,
                    CONF_FRIENDLY_NAME: friendly_name,
                },
            )

        return self.async_show_form(
            step_id="device_options",
            data_schema=STEP_DEVICE_OPTIONS_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Standard-Reconfigure: Bestehende Instanz korrigieren (z.B. neues Passwort, Device ID, Model)."""
        entry = self._get_reconfigure_entry()

        errors = {}

        if user_input is not None:
            merged = {**entry.data, **user_input}

            try:
                await validate_input_for_device(self.hass, merged)
                # Entry aktualisieren
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=merged,
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfiguration_completed")
            except HomeAssistantError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._get_reconfigure_schema(entry),
            errors=errors,
        )

    def _get_reconfigure_schema(self, entry: ConfigEntry) -> vol.Schema:
        """Hilfsfunktion, um Schema für Reconfigure zu erstellen."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_ID, default=entry.data.get(CONF_DEVICE_ID, "")
                ): str,
                vol.Required(CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_MODEL_ID, default=entry.data.get(CONF_MODEL_ID, "83")
                ): selector(
                    {
                        "select": {
                            "options": [
                                {"label": "PowerOcean", "value": "83"},
                                {"label": "PowerOcean DC Fit", "value": "85"},
                                {"label": "PowerOcean Single Phase", "value": "86"},
                                {"label": "PowerOcean Plus", "value": "87"},
                            ],
                            "mode": "dropdown",
                        }
                    }
                ),
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PowerOceanOptionsFlow()


class PowerOceanOptionsFlow(OptionsFlow):
    """Handhabt Einstellungen, die jederzeit änderbar sind."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Erster Schritt des Options Flows."""
        if user_input is not None:
            # Erzeugt/aktualisiert das 'options' Dictionary im ConfigEntry
            return self.async_create_entry(title="", data=user_input)

        # Zugriff auf self.config_entry ist hier direkt möglich
        options = self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FRIENDLY_NAME,
                        default=options.get(CONF_FRIENDLY_NAME, DEFAULT_NAME),
                    ): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): selector(
                        {
                            "number": {
                                "min": 10,
                                "max": 60,
                                "unit_of_measurement": "s",
                                "mode": "box",
                            }
                        }
                    ),
                }
            ),
        )


# Helper function to sanitize
def sanitize_device_name(
    device_name: str, fall_back: str, max_length: int = 255
) -> str:
    """
    Sanitize the device name.

    by trimming whitespace, removing special characters, and enforcing a maximum length.
    """
    # Trim whitespace
    sanitized = device_name.strip()

    # Remove disallowed characters
    sanitized = re.sub(r"[^\w\s\-]", "", sanitized)

    # Collapse multiple spaces to a single space
    sanitized = re.sub(r"\s+", " ", sanitized)

    # Enforce max length
    sanitized = sanitized[:max_length]

    # Fallback if name is empty after sanitization
    if not sanitized:
        return fall_back[:max_length]

    return sanitized
