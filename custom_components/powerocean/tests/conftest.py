# tests/conftest.py
import pytest

from custom_components.powerocean.ecoflow import Ecoflow


@pytest.fixture(scope="session")
def eco() -> Ecoflow:
    """
    Shared Ecoflow instance for unit tests.

    Created once per test run.
    """
    return Ecoflow(
        serialnumber="SN_TEST",
        username="user",
        password="pass",
        variant="default",
        options=None,
    )
