import pytest

from mqrc.dr.wire import NotDRv1Error, build_body, parse_body, split_framed


def test_roundtrip_preserves_identity_and_payload():
    body = build_body(seq=42, uuid="abc-123", session_date="20260608", payload="MSG-0042")
    msg = parse_body(body)
    assert msg.seq == 42
    assert msg.uuid == "abc-123"
    assert msg.session_date == "20260608"
    assert msg.payload == "MSG-0042"


def test_body_is_bytes_and_self_delimited():
    body = build_body(seq=1, uuid="u1", session_date="20260608", payload="T|with|pipes")
    assert isinstance(body, bytes)
    # the payload field may contain the delimiter; parsing must still recover it
    assert parse_body(body).payload == "T|with|pipes"


def test_parse_rejects_non_drv1_body():
    with pytest.raises(ValueError, match="not a DRv1 body"):
        parse_body(b"not-a-dr-message")


def test_not_drv1_error_is_a_value_error():
    assert issubclass(NotDRv1Error, ValueError)  # existing ValueError handlers still apply


@pytest.mark.parametrize(
    "body",
    [
        b"DRv1|not-a-seq|u|20260608|p",  # non-integer seq
        b"DRv1|1|u|\xff\xfe|p",  # not UTF-8
        b"DRv1|1|u",  # too few fields
    ],
)
def test_parse_rejects_a_malformed_drv1_body(body):
    with pytest.raises(NotDRv1Error, match="not a DRv1 body"):
        parse_body(body)


def test_split_framed_finds_the_body_after_a_header():
    body = build_body(seq=9, uuid="u9", session_date="20260608", payload="P")
    found, msg = split_framed(b"HEADER--" + body)
    assert found == body
    assert (msg.seq, msg.uuid) == (9, "u9")


def test_split_framed_rejects_a_message_without_the_marker():
    # #1550: find() == -1 must never become raw[-1:] (the last byte) and be parsed
    with pytest.raises(NotDRv1Error, match="no DRv1 body"):
        split_framed(b"REPLY:xxxxxxxx")
