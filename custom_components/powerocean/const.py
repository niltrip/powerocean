"""Constants for the PowerOcean integration."""

import logging

from homeassistant.const import Platform

DOMAIN = "powerocean"
ISSUE_URL = "https://github.com/niltrip/powerocean/issues"
ISSUE_URL_ERROR_MESSAGE = " Please log any issues here: " + ISSUE_URL
LENGTH_BATTERIE_SN = 12  # Length of the battery serial number to identify battery data
USE_MOCKED_RESPONSE = False  # Set to True to use mocked responses for testing

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")

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
