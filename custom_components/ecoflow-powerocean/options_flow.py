"""
Options flow handler for the PowerOcean Home Assistant integration.

This module defines the PowerOceanOptionsFlowHandler class, which manages
the options flow for configuring PowerOcean integration options such as
friendly name and scan interval.
"""  # noqa: EXE002

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_SCAN_INTERVAL

from .config_flow import sanitize_device_name


class PowerOceanOptionsFlowHandler(OptionsFlow):
    """Handle options for the PowerOcean integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize PowerOcean options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
            except ValueError:
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
