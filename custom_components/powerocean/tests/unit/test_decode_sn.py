"""test_decode_sn."""


def test_decode_sn_base64(parser) -> None:
    """Decode SN base64."""
    assert parser._decode_sn("U05fVEVTVA==") == "SN_TEST"


def test_decode_sn_plaintext(parser) -> None:
    """Decode SN plain."""
    assert parser._decode_sn("SN_PLAIN") == "SN_PLAIN"


def test_decode_sn_none(parser) -> None:
    """Decode SN none."""
    assert parser._decode_sn(None) is None


def test_decode_sn_empty_string(parser) -> None:
    """Decode SN empty."""
    assert parser._decode_sn("") is None
