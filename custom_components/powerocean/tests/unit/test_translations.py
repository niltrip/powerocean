"""Translated states: every key has texts, in the format hassfest accepts."""

import json
import re
from pathlib import Path

import pytest

from custom_components.powerocean.types import translation_key
from custom_components.powerocean.utils import TRANSLATED_STATES

COMPONENT_DIR = Path(__file__).parent.parent.parent
FILES = ["strings.json", "translations/en.json", "translations/de.json"]
# As hassfest checks translation keys: lowercase, no leading/double "_"
TRANSLATION_KEY = re.compile(r"^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$")


def _sensor_translations(file: str) -> dict:
    data = json.loads((COMPONENT_DIR / file).read_text("utf-8"))
    return data["entity"]["sensor"]


def test_translation_key() -> None:
    assert translation_key("emsWordMode") == "ems_word_mode"
    assert translation_key("sys14aEnable") == "sys14a_enable"


@pytest.mark.parametrize("file", FILES)
def test_every_translated_key_has_states(file: str) -> None:
    sensors = _sensor_translations(file)
    assert set(sensors) == {translation_key(key) for key in TRANSLATED_STATES}
    for name, entry in sensors.items():
        assert TRANSLATION_KEY.match(name), name
        assert entry["state"], name
        for state in entry["state"]:
            assert TRANSLATION_KEY.match(state), f"{name}: {state}"


@pytest.mark.parametrize("file", FILES[1:])
def test_translations_have_the_states_of_strings(file: str) -> None:
    strings = _sensor_translations("strings.json")
    sensors = _sensor_translations(file)
    for name, entry in strings.items():
        assert set(sensors[name]["state"]) == set(entry["state"]), name


@pytest.fixture
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Enable custom integrations in HA tests."""
    return


async def test_home_assistant_loads_the_states(
    hass, enable_custom_integrations_fixture
) -> None:
    from homeassistant.helpers.translation import (  # noqa: PLC0415
        async_get_translations,
    )

    texts = await async_get_translations(hass, "de", "entity", {"powerocean"})
    prefix = "component.powerocean.entity.sensor"
    assert texts[f"{prefix}.ems_word_mode.state.workmode_selfuse"] == "Eigenbedarf"
    assert texts[f"{prefix}.pcs_run_sta.state.runsta_stop"] == "Gestoppt"
