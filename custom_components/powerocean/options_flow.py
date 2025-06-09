from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_SCAN_INTERVAL, CONF_CHOOSE

from .const import DOMAIN
from .config_flow import sanitize_device_name

OPTIONS = ["PV", "Battery", "Grid"]


class PowerOceanOptionsFlowHandler(OptionsFlow):
    """Handle options for the PowerOcean integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize PowerOcean options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the PowerOcean options."""
        errors = {}
        if user_input is not None:
            try:
                device_name = self.config_entry.data["device_info"].get(
                    "name", "PowerOcean"
                )
                user_input[CONF_FRIENDLY_NAME] = sanitize_device_name(
                    user_input[CONF_FRIENDLY_NAME], device_name
                )
                return self.async_create_entry(title="", data=user_input)
            except Exception as e:
                errors["base"] = "reconfig_error"

        # Lade vorhandene Optionen oder verwende Defaults
        options = self.config_entry.data.get("options", {})
        step_device_options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_FRIENDLY_NAME,
                    default=options.get(CONF_FRIENDLY_NAME, "PowerOcean"),
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL)
                ): int,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=step_device_options_schema,
            errors=errors,
        )
