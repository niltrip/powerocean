"""test_extract_box_sn."""


def test_extract_box_sn_from_payload(eco) -> None:
    payload = {"info": {"sn": "U05fVEVTVA=="}}
    schema = {"sn_path": ["info", "sn"]}
    assert eco._extract_box_sn(payload, schema, "FALLBACK") == "SN_TEST"


def test_extract_box_sn_fallback(eco) -> None:
    payload = {}
    schema = {"sn_path": None}
    assert eco._extract_box_sn(payload, schema, "SN_FALLBACK") == "SN_FALLBACK"


def test_extract_box_sn_invalid(eco) -> None:
    payload = {"sn": 123}
    schema = {"sn_path": ["sn"]}
    assert eco._extract_box_sn(payload, schema, "FALLBACK") is None
