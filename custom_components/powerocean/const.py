"""Constants for the Resol integration."""
import logging
from homeassistant.const import Platform

DOMAIN = "resol"                    # Have requested to add logos via https://github.com/home-assistant/brands/pull/4904
NAME = "Resol Controller"
VERSION="2023.11.1"
ISSUE_URL = "https://github.com/evercape/resol/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL


PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger("custom_components.resol")

ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_DESTINATION_NAME = "Destination Name"
ATTR_SOURCE_NAME = "Source Name"
ATTR_UNIQUE_ID = "Internal Unique ID"
ATTR_PRODUCT_VENDOR = "Vendor"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"
ATTR_PRODUCT_NAME = "Device Name"
ATTR_PRODUCT_VERSION = "Vendor Firmware Version"
ATTR_PRODUCT_BUILD = "Vendor Product Build"
ATTR_PRODUCT_FEATURES = "Vendor Product Features"




STARTUP_MESSAGE = f"""
----------------------------------------------------------------------------
{NAME}
Version: {VERSION}
Domain: {DOMAIN}
If you have any issues with this custom component please open an issue here:
{ISSUE_URL}
----------------------------------------------------------------------------
"""
