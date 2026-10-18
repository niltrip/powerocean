"""Constants for the PowerOcean integration."""

from logging import Logger, getLogger

from homeassistant.const import Platform

from .devices import MODEL_NAME_MAP, PowerOceanModel  # noqa: F401 (re-export)

LOGGER: Logger = getLogger(__package__)
DOMAIN = "powerocean"
DEFAULT_SCAN_INTERVAL = 10
DEFAULT_NAME = "PowerOcean"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL
PLATFORMS: list[Platform] = [Platform.SENSOR]
ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"
