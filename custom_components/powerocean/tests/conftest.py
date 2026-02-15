"""tests/conftest.py."""

import pytest

from custom_components.powerocean.parser import EcoflowParser


@pytest.fixture(scope="session")
def parser() -> EcoflowParser:
    """
    Shared Ecoflow instance for unit tests.

    Created once per test run.
    """
    return EcoflowParser(
        variant="default",
        sn="SN_INVERTERBOX01",
    )
