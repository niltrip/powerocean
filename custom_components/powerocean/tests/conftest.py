"""tests/conftest.py."""

import pytest

from custom_components.powerocean.ecoflow import EcoflowApi


@pytest.fixture(scope="session")
def eco() -> EcoflowApi:
    """
    Shared Ecoflow instance for unit tests.

    Created once per test run.
    """
    return EcoflowApi(
        hass=None,
        serialnumber="SN_INVERTERBOX01",
        username="user",
        password="pass",  # noqa: S106
        variant="default",
    )
