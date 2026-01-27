"""test_get_nested_value."""

from custom_components.powerocean.ecoflow import Ecoflow


def test_get_nested_value_success() -> None:
    data = {"a": {"b": {"c": 42}}}
    assert Ecoflow._get_nested_value(data, ["a", "b", "c"]) == 42


def test_get_nested_value_missing_key() -> None:
    data = {"a": {"b": {}}}
    assert Ecoflow._get_nested_value(data, ["a", "b", "c"]) is None


def test_get_nested_value_wrong_type() -> None:
    data = {"a": 123}
    assert Ecoflow._get_nested_value(data, ["a", "b"]) is None
