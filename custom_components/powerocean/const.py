"""Constants for the PowerOcean integration."""

from enum import StrEnum
from logging import Logger, getLogger
from pathlib import Path

from homeassistant.const import Platform

LOGGER: Logger = getLogger(__package__)
DOMAIN = "powerocean"
DEFAULT_SCAN_INTERVAL = 10
DEFAULT_NAME = "PowerOcean"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL
USE_MOCKED_RESPONSE = False  # Set to True to use mocked responses for testing
# Mock path to response.json file
MOCKED_RESPONSE = (
    Path(__file__).parent / "tests" / "fixtures" / "response_modified.json"
)
PLATFORMS: list[Platform] = [Platform.SENSOR]
ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"


class PowerOceanModel(StrEnum):
    """Enumeration of supported PowerOcean device models with their internal codes."""

    POWEROCEAN = "83"
    POWEROCEAN_DC_FIT = "85"
    POWEROCEAN_SINGLE_PHASE = "86"
    POWEROCEAN_PLUS = "87"


MODEL_NAME_MAP = {
    PowerOceanModel.POWEROCEAN: "PowerOcean",
    PowerOceanModel.POWEROCEAN_DC_FIT: "PowerOcean DC fit",
    PowerOceanModel.POWEROCEAN_SINGLE_PHASE: "PowerOcean Single Phase",
    PowerOceanModel.POWEROCEAN_PLUS: "PowerOcean Plus",
}
