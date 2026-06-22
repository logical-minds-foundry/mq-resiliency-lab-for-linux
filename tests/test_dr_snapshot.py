from mqlab.dr.snapshot import seqs_from_bodies
from mqlab.dr.wire import build_body


def test_extracts_seqs_and_ignores_foreign_bodies():
    bodies = [
        build_body(seq=10, uuid="u10", session_date="20260608", payload="T10"),
        build_body(seq=11, uuid="u11", session_date="20260608", payload="T11"),
        b"not-a-dr-message",  # foreign traffic must be ignored, not crash
    ]
    assert seqs_from_bodies(bodies) == {10, 11}


def test_empty_browse_is_empty_set():
    assert seqs_from_bodies([]) == set()
