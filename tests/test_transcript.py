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


def test_transcript_accepts_runs_under_symlinked_shared_state(tmp_path, monkeypatch):
    # A git worktree whose state/ bucket is a symlink to the primary checkout's
    # build/state (the shared-bucket model, #69). The transcript's resolved path
    # lands under the primary build/, not the worktree's — it must still be
    # accepted so the lab can be driven from a worktree.
    primary_state = tmp_path / "primary" / "build" / "state"
    primary_state.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    (worktree / "build").mkdir(parents=True)
    (worktree / "build" / "state").symlink_to(primary_state)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(worktree))

    path = transcript_path("vm-status", "20260625T165216Z")
    with Transcript(path) as t:
        t.write("ok")

    written = primary_state / "runs" / "20260625T165216Z-vm-status.log"
    assert written.read_text(encoding="utf-8") == "ok\n"
