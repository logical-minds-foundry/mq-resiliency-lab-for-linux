from __future__ import annotations

import pytest

from mqlab.runreport import RunMetadata, capture_metadata


def test_capture_metadata_assembles_from_readers() -> None:
    md = capture_metadata(
        "distributed",
        "20260615T143000Z",
        commit_reader=lambda: "abc123",
        digest_reader=lambda: "deadbeef",
        version_reader=lambda: {"vagrant": "2.4.1"},
    )
    assert md == RunMetadata(
        setup="distributed",
        commit="abc123",
        timestamp="20260615T143000Z",
        config_digest="deadbeef",
        versions={"vagrant": "2.4.1"},
    )


def test_capture_metadata_propagates_reader_failure() -> None:
    def boom() -> str:
        raise RuntimeError("git unavailable")

    with pytest.raises(RuntimeError, match="git unavailable"):
        capture_metadata(
            "distributed",
            "20260615T143000Z",
            commit_reader=boom,
            digest_reader=lambda: "d",
            version_reader=dict,
        )


def test_run_metadata_to_dict_round_trips() -> None:
    md = RunMetadata("s", "c", "t", "d", {"k": "v"})
    assert md.to_dict() == {
        "setup": "s",
        "commit": "c",
        "timestamp": "t",
        "config_digest": "d",
        "versions": {"k": "v"},
    }
