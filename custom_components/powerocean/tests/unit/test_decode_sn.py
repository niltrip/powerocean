"""test_decode_sn."""


def test_decode_sn_base64(eco) -> None:
    assert eco._decode_sn("U05fVEVTVA==") == "SN_TEST"


def test_decode_sn_plaintext(eco) -> None:
    assert eco._decode_sn("SN_PLAIN") == "SN_PLAIN"


def test_decode_sn_none(eco) -> None:
    assert eco._decode_sn(None) is None


def test_decode_sn_empty_string(eco) -> None:
    assert eco._decode_sn("") is None
