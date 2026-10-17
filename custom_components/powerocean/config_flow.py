"""
Config flow for the PowerOcean integration.

This module defines the configuration flow and options flow for
adding and managing PowerOcean devices in Home Assistant.

Features:
- User authentication via EcoFlow API.
- Device model selection.
- Optional configuration for friendly name and scan interval.
- Reconfiguration of existing entries.
- Re-authentication when the stored credentials stop working.
- Sanitization of device names to ensure valid formatting.

Classes:
- PowerOceanConfigFlow: Handles the main configuration flow.
- PowerOceanOptionsFlow: Handles options for existing entries.

Functions:
- validate_input_for_device: Validates user input by attempting login.
- sanitize_device_name: Cleans and validates device names.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_FRIENDLY_NAME,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AuthenticationError, EcoflowApiError
from .const import (
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MODEL_NAME_MAP,
    PowerOceanModel,
)
from .ecoflow import HAEcoflowApi

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
MODEL_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=model.value, label=MODEL_NAME_MAP[model])
            for model in PowerOceanModel
        ],
        mode=SelectSelectorMode.DROPDOWN,
    )
)
SCAN_INTERVAL_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=10,
        max=60,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)

# Schema for the user step and the reconfigure step
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Required(CONF_MODEL_ID, default=PowerOceanModel.POWEROCEAN.value): (
            MODEL_SELECTOR
        ),
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): EMAIL_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)


def device_options_schema(
    friendly_name: str = DEFAULT_NAME,
    scan_interval: float = DEFAULT_SCAN_INTERVAL,
) -> vol.Schema:
    """Build the schema for device name and polling interval."""
    return vol.Schema(
        {
            vol.Required(CONF_FRIENDLY_NAME, default=friendly_name): str,
            vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): (
                SCAN_INTERVAL_SELECTOR
            ),
        }
    )


class CannotConnectError(HomeAssistantError):
    """Error to indicate the EcoFlow cloud or the device could not be reached."""


class InvalidAuthError(HomeAssistantError):
    """Error to indicate the credentials were rejected."""


async def validate_input_for_device(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """
    Validate the user input allows us to connect.

    Raises:
        InvalidAuthError: If the EcoFlow cloud rejected the credentials.
        CannotConnectError: If the cloud or the device could not be reached.

    """
    api = HAEcoflowApi(
        hass,
        data[CONF_DEVICE_ID],
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
        data[CONF_MODEL_ID],
    )

    try:
        await api.async_authorize()
    except AuthenticationError as err:
        raise InvalidAuthError from err
    except EcoflowApiError as err:
        raise CannotConnectError from err


class PowerOceanConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    Handle the configuration flow for adding a PowerOcean device to Home Assistant.

    This flow guides the user through:
      1. Authenticating with the EcoFlow API.
      2. Selecting the device model.
      3. Providing optional device settings (friendly name, scan interval).
    """

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Instanzvariablen für den Flow-Verlauf."""
        self._cloud_data: dict[str, Any] = {}

    async def _async_validate(self, data: dict[str, Any]) -> dict[str, str]:
        """Validate credentials and return form errors (empty on success)."""
        try:
            await validate_input_for_device(self.hass, data)
        except InvalidAuthError:
            return {"base": "invalid_auth"}
        except CannotConnectError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - show "unknown" instead of crashing
            LOGGER.exception("Unexpected error while validating PowerOcean login")
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Unique ID zuerst prüfen, damit Duplikate keinen Login auslösen
            await self.async_set_unique_id(f"PowerOcean {user_input[CONF_DEVICE_ID]}")
            self._abort_if_unique_id_configured()

            errors = await self._async_validate(user_input)
            if not errors:
                # Daten für Schritt 2 zwischenspeichern
                self._cloud_data = user_input
                return await self.async_step_device_options()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 2: Zusätzliche Optionen abfragen."""
        if user_input is not None:
            model_name = MODEL_NAME_MAP[
                PowerOceanModel(self._cloud_data[CONF_MODEL_ID])
            ]
            friendly_name = sanitize_device_name(
                user_input[CONF_FRIENDLY_NAME],
                fall_back=DEFAULT_NAME,
            )
            return self.async_create_entry(
                title=model_name,
                data=self._cloud_data,
                options={
                    **user_input,
                    CONF_FRIENDLY_NAME: friendly_name,
                },
            )

        return self.async_show_form(
            step_id="device_options",
            data_schema=device_options_schema(),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Standard-Reconfigure.

        Bestehende Instanz korrigieren (z.B. Passwort, E-Mail, Modell).
        Die Seriennummer muss gleich bleiben: alle Entities hängen an ihr,
        ein anderes Gerät gehört in einen eigenen Config Entry.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            # Entspricht _abort_if_unique_id_mismatch(reason="wrong_device"),
            # vergleicht aber die Seriennummer direkt: ältere Entries haben
            # noch Unique IDs in früheren Formaten ("PowerOcean_<SN>" u.a.).
            if user_input[CONF_DEVICE_ID] != entry.data[CONF_DEVICE_ID]:
                return self.async_abort(reason="wrong_device")

            errors = await self._async_validate({**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        suggested = {
            key: value
            for key, value in (user_input or entry.data).items()
            if key != CONF_PASSWORD
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],  # noqa: ARG002 - required by HA signature
    ) -> ConfigFlowResult:
        """Start re-authentication after the stored credentials were rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and update the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_validate({**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_DATA_SCHEMA,
                {CONF_EMAIL: (user_input or entry.data).get(CONF_EMAIL)},
            ),
            description_placeholders={"device_id": entry.data[CONF_DEVICE_ID]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PowerOceanOptionsFlow:
        """
        Return the options flow handler for this config entry.

        This method is called by Home Assistant to retrieve
        an OptionsFlow instance that allows the user to adjust
        settings for an existing PowerOcean integration entry.

        Args:
            config_entry (ConfigEntry): The configuration entry for which
                the options flow is requested.

        Returns:
            OptionsFlow: An instance of the PowerOceanOptionsFlow class.

        """
        return PowerOceanOptionsFlow()


class PowerOceanOptionsFlow(OptionsFlowWithReload):
    """
    Handle adjustable options for an existing PowerOcean config entry.

    This flow allows the user to update:
      - Friendly name of the device.
      - Scan interval for periodic updates.

    The entry is reloaded automatically after the options were saved.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Erster Schritt des Options Flows."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **user_input,
                    CONF_FRIENDLY_NAME: sanitize_device_name(
                        user_input[CONF_FRIENDLY_NAME], fall_back=DEFAULT_NAME
                    ),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=device_options_schema(
                friendly_name=options.get(CONF_FRIENDLY_NAME, DEFAULT_NAME),
                scan_interval=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
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
