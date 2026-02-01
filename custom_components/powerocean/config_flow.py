"""config_flow.py: Config flow for PowerOcean integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.exceptions import HomeAssistantError, IntegrationError
from homeassistant.helpers.selector import selector

from .const import DOMAIN, ISSUE_URL_ERROR_MESSAGE
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

# This are the step's schema when setting up the integration, or its devices
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID, default=""): str,
        vol.Required(CONF_EMAIL, default=""): str,
        vol.Required(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_MODEL_ID, default="83"): selector(
            {
                "select": {
                    "options": [
                        {
                            "label": "PowerOcean",
                            "value": "83",
                        },
                        {
                            "label": "PowerOcean DC Fit",
                            "value": "85",
                        },
                        {
                            "label": "PowerOcean Single Phase",
                            "value": "86",
                        },
                        {
                            "label": "PowerOcean Plus",
                            "value": "87",
                        },
                    ],
                    "mode": "dropdown",
                }
            }
        ),
    }
)

STEP_DEVICE_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_FRIENDLY_NAME, default="PowerOcean"): str,
        vol.Required(CONF_SCAN_INTERVAL, default=10): selector(
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
        # Return the device object with the device information
        return await hass.async_add_executor_job(ecoflow.get_device)

    # Exception if device cannot be found
    except IntegrationError as e:
        _LOGGER.error(
            f"Failed to connect to PowerOcean device: {e}" + ISSUE_URL_ERROR_MESSAGE
        )
        raise CannotConnectError from e

    # Exception if authentication fails
    except AuthenticationFailedError as e:
        _LOGGER.error(f"Authentication failed: {e}" + ISSUE_URL_ERROR_MESSAGE)
        raise InvalidAuthError from e


async def validate_settings(hass: HomeAssistant, data: dict[str, Any]) -> bool:  # noqa: ARG001
    """Another validation method for our config steps."""
    return True


class PowerOceanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PowerOcean."""

    VERSION = 1.3

    def __init__(self) -> None:
        """Initialize the PowerOceanConfigFlow instance."""
        self.user_input_from_step_user = {}
        self._title: str
        self.options = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                device = await validate_input_for_device(self.hass, user_input)
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                unique_id = f"{device['product']}_{device['serial']}"
                await self.async_set_unique_id(unique_id)

                self.user_input_from_step_user = user_input
                self.device_info = device

                return await self.async_step_device_options(user_input=None)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the device options step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_input[CONF_FRIENDLY_NAME] = sanitize_device_name(
                    user_input[CONF_FRIENDLY_NAME], self.device_info["name"]
                )

                self._title = user_input[CONF_FRIENDLY_NAME]
                if not await validate_settings(self.hass, user_input):
                    errors["base"] = "invalid_settings"

                if "base" not in errors:
                    return self.async_create_entry(
                        title=self._title,
                        data={
                            "user_input": self.user_input_from_step_user,  # from step 1
                            "device_info": self.device_info,  # from device detection
                            "options": user_input,  # new options from this step
                        },
                    )
            except ValueError as e:
                _LOGGER.error(
                    f"Failed to handle device options: {e}" + ISSUE_URL_ERROR_MESSAGE
                )
                errors["base"] = "option_error"

        return self.async_show_form(
            step_id="device_options",
            data_schema=STEP_DEVICE_OPTIONS_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration options."""
        errors: dict[str, str] = {}

        # Hole aktuellen ConfigEntry für Re-Konfiguration
        current_entry = self._get_reconfigure_entry()
        current_options = current_entry.options or {}

        # Standardname aus Optionen setzen (Fallback auf Default)
        default_name = current_options.get(CONF_FRIENDLY_NAME, "PowerOcean")

        if user_input is not None:
            try:
                # Unique ID setzen und sicherstellen,
                # dass es sich um die korrekte Instanz handelt
                await self.async_set_unique_id(current_entry.unique_id)
                self._abort_if_unique_id_mismatch()

                # Gerätname bereinigen
                user_input[CONF_FRIENDLY_NAME] = sanitize_device_name(
                    user_input[CONF_FRIENDLY_NAME], default_name
                )

                # Optionen aktualisieren
                updated_options = {**current_options, **user_input}

                # Eintrag aktualisieren und neu laden
                return self.async_update_reload_and_abort(
                    current_entry, data_updates={"options": updated_options}
                )

            except ValueError as e:
                _LOGGER.exception(f"Fehler bei der Re-Konfiguration: {e}")
                errors["base"] = "reconfig_error"

        # Formular mit bestehenden Werten anzeigen
        data_schema = vol.Schema(
            {
                vol.Required(CONF_FRIENDLY_NAME, default=default_name): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_options.get(CONF_SCAN_INTERVAL, 10),
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
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )


class CannotConnectError(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuthError(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class AuthenticationFailedError(HomeAssistantError):
    """Error to indicate authentication failure."""

    def __init__(self) -> None:
        """Initialize AuthenticationFailedError with a default message."""
        msg = "Invalid authentication!"
        super().__init__(msg)


# Helper function to sanitize
def sanitize_device_name(
    device_name: str, fall_back: str, max_length: int = 255
) -> str:
    """
    Sanitize the device name by trimming whitespace,
    removing special characters, and enforcing a maximum length.

    Args:
        device_name (str): The device name to sanitize.
        fall_back (str): The fallback name to use if the sanitized name is empty.
        max_length (int, optional): The maximum allowed length for the name.
            Defaults to 255.

    Returns:
        str: The sanitized device name.

    """  # noqa: D205
    # Trim whitespace
    sanitized = device_name.strip()

    # Remove disallowed characters
    # (keep alphanumerics, spaces, underscores, and hyphens)
    sanitized = re.sub(r"[^\w\s\-]", "", sanitized)

    # Collapse multiple spaces to a single space
    sanitized = re.sub(r"\s+", " ", sanitized)

    # Enforce max length
    sanitized = sanitized[:max_length]

    # Fallback if name is empty after sanitization
    if not sanitized:
        return fall_back[:max_length]

    return sanitized
