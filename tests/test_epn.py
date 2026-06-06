import pytest

from mqlab.epn import (
    ACK_BAD_HEADER,
    ACK_OK,
    ACK_STALE_DATE,
    pack_header,
    parse_header,
    validate_header,
)


def test_pack_header_fixed_width():
    h = pack_header(password="pw", sender="FIRM01", receiver="DTCCSVC", busdate="20260606")
    assert len(h) == 32
    assert h == "pw      FIRM01  DTCCSVC 20260606"


def test_roundtrip():
    h = pack_header(password="pw", sender="FIRM01", receiver="DTCCSVC", busdate="20260606")
    f = parse_header(h + "PAYLOAD")
    assert (f.sender, f.receiver, f.busdate, f.payload) == (
        "FIRM01",
        "DTCCSVC",
        "20260606",
        "PAYLOAD",
    )


def test_pack_header_rejects_overlong_field():
    with pytest.raises(ValueError, match="sender exceeds"):
        pack_header(password="pw", sender="TOOLONGFIRM", receiver="DTCCSVC", busdate="20260606")


def test_validate_rejects_blank_required_fields():
    blank_sender = pack_header(password="pw", sender="", receiver="DTCCSVC", busdate="20260606")
    assert validate_header(blank_sender, today="20260606") == ACK_BAD_HEADER


def test_validate_ack_codes():
    good = pack_header(password="pw", sender="FIRM01", receiver="DTCCSVC", busdate="20260606")
    assert validate_header(good, today="20260606") == ACK_OK
    assert validate_header("short", today="20260606") == ACK_BAD_HEADER
    stale = pack_header(password="pw", sender="FIRM01", receiver="DTCCSVC", busdate="20250101")
    assert validate_header(stale, today="20260606") == ACK_STALE_DATE
