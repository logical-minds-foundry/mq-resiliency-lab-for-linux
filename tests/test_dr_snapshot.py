from mqlab.dr.snapshot import seqs_from_bodies
from mqlab.dr.wire import build_body


def test_extracts_seqs_and_ignores_foreign_bodies():
    bodies = [
        build_body(seq=10, uuid="u10", busdate="20260608", trade="T10"),
        build_body(seq=11, uuid="u11", busdate="20260608", trade="T11"),
        b"not-a-dr-message",  # foreign traffic must be ignored, not crash
    ]
    assert seqs_from_bodies(bodies) == {10, 11}


def test_empty_browse_is_empty_set():
    assert seqs_from_bodies([]) == set()
