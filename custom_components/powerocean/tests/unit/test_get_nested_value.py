"""test_get_nested_value."""


def test_get_nested_value_success(parser) -> None:
    data = {"a": {"b": {"c": 42}}}
    assert parser._get_nested_value(data, ["a", "b", "c"]) == 42


def test_get_nested_value_missing_key(parser) -> None:
    data = {"a": {"b": {}}}
    assert parser._get_nested_value(data, ["a", "b", "c"]) is None


def test_get_nested_value_wrong_type(parser) -> None:
    data = {"a": 123}
    assert parser._get_nested_value(data, ["a", "b"]) is None
