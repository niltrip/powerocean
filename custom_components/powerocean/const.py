"""Constants for the PowerOcean integration."""

import logging
from homeassistant.const import Platform

DOMAIN = "powerocean"  # Have requested to add logos via https://github.com/home-assistant/brands/pull/4904
NAME = "Ecoflow PowerOcean"
VERSION = "2024.08.25"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL


PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger("custom_components.powerocean")

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
