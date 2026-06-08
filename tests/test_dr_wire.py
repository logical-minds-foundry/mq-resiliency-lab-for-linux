import pytest

from mqlab.dr.wire import build_body, parse_body


def test_roundtrip_preserves_identity_and_payload():
    body = build_body(seq=42, uuid="abc-123", busdate="20260608", trade="TRADE-0042")
    msg = parse_body(body)
    assert msg.seq == 42
    assert msg.uuid == "abc-123"
    assert msg.busdate == "20260608"
    assert msg.trade == "TRADE-0042"


def test_body_is_bytes_and_self_delimited():
    body = build_body(seq=1, uuid="u1", busdate="20260608", trade="T|with|pipes")
    assert isinstance(body, bytes)
    # the trade field may contain the delimiter; parsing must still recover it
    assert parse_body(body).trade == "T|with|pipes"


def test_parse_rejects_non_drv1_body():
    with pytest.raises(ValueError, match="not a DRv1 body"):
        parse_body(b"not-a-dr-message")
