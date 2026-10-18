"""Constants for the PowerOcean integration."""

from logging import Logger, getLogger
from pathlib import Path

from homeassistant.const import Platform

from .devices import MODEL_NAME_MAP, PowerOceanModel  # noqa: F401 (re-export)

LOGGER: Logger = getLogger(__package__)
DOMAIN = "powerocean"
DEFAULT_SCAN_INTERVAL = 10
DEFAULT_NAME = "PowerOcean"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
# EcoFlow web portal for owners (portal.ecoflow.com is the installer portal)
PORTAL_URL = "https://user-portal.ecoflow.com/"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL
USE_MOCKED_RESPONSE = False  # Set to True to use mocked responses for testing
# Mock path to response.json file
MOCKED_RESPONSE = (
    Path(__file__).parent / "tests" / "fixtures" / "response_modified.json"
)
PLATFORMS: list[Platform] = [Platform.SENSOR]
ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"
