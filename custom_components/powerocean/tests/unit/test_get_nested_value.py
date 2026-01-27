# tests/unit/test_get_nested_value.py
from custom_components.powerocean.ecoflow import Ecoflow


def test_get_nested_value_success():
    data = {"a": {"b": {"c": 42}}}
    assert Ecoflow._get_nested_value(None, data, ["a", "b", "c"]) == 42


def test_get_nested_value_missing_key():
    data = {"a": {"b": {}}}
    assert Ecoflow._get_nested_value(None, data, ["a", "b", "c"]) is None


def test_get_nested_value_wrong_type():
    data = {"a": 123}
    assert Ecoflow._get_nested_value(None, data, ["a", "b"]) is None
