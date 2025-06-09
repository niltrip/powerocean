"""Options flow for PowerOcean integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CHOOSE, CONF_FRIENDLY_NAME, CONF_SCAN_INTERVAL
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN


class PowerOceanOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle PowerOcean options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize PowerOcean options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options."""
        errors: dict[str, str] = {}
        current = self.config_entry.options

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FRIENDLY_NAME,
                        default=current.get(CONF_FRIENDLY_NAME, "PowerOcean"),
                    ): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=current.get(CONF_SCAN_INTERVAL, 10),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=60)),
                    vol.Required(
                        CONF_CHOOSE,
                        default=current.get(
                            CONF_CHOOSE,
                            ["ENERGY_STREAM_REPORT", "EMS_CHANGE_REPORT"],
                        ),
                    ): cv.multi_select(
                        [
                            "ENERGY_STREAM_REPORT",
                            "EMS_CHANGE_REPORT",
                            "EVCHARGING_REPORT",
                        ]
                    ),
                }
            ),
            errors=errors,
        )
