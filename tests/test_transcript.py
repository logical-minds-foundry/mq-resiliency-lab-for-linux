from __future__ import annotations

import pytest

from mqlab.paths import runs_dir
from mqlab.transcript import Transcript, TranscriptError, transcript_path


def test_transcript_path_names_by_timestamp_and_verb():
    expected = runs_dir() / "20260609T101500Z-net-up.log"
    assert transcript_path("net-up", "20260609T101500Z") == expected


def test_transcript_writes_lines_under_build(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    path = transcript_path("net-up", "20260609T101500Z")
    with Transcript(path) as t:
        t.write("$ virsh net-start net-wan")
        t.write("Network net-wan started")
    expected = "$ virsh net-start net-wan\nNetwork net-wan started\n"
    assert path.read_text(encoding="utf-8") == expected


def test_transcript_refuses_paths_outside_build(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    with pytest.raises(TranscriptError):
        Transcript(tmp_path / "docs" / "reports" / "leak.log")
