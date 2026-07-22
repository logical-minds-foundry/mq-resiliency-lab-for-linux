"""Tests for the pre-run venv freshness guard (#776).

`ensure_venv_current` runs `uv sync` up front so the checkout's `.venv` matches
`uv.lock` before the lab-lifecycle verbs spawn venv-dependent subprocesses. The
`which`/`run` seams are injected so no real `uv` runs here.
"""

from __future__ import annotations

import subprocess

import pytest
import typer

from mqlab import venvsync

# Captured at import (before the autouse conftest fixture stubs the module
# attribute) so these tests exercise the REAL helper, not the neutralised stub.
_ensure = venvsync.ensure_venv_current


def _completed(
    returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["uv", "sync"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_sync_runs_when_uv_present(capsys):
    calls: list[str] = []

    def run(uv: str) -> subprocess.CompletedProcess[str]:
        calls.append(uv)
        return _completed(0, stdout="Resolved 1 package")

    _ensure(which=lambda _tool: "/usr/bin/uv", run=run)

    assert calls == ["/usr/bin/uv"]  # the resolved uv path was invoked
    assert "syncing lab environment" in capsys.readouterr().err


def test_uv_not_found_warns_and_continues(capsys):
    def run(_uv: str) -> subprocess.CompletedProcess[str]:
        pytest.fail("uv sync must not run when uv is absent")

    # Returns (does not raise) so a working non-uv environment is not hard-failed.
    _ensure(which=lambda _tool: None, run=run)

    err = capsys.readouterr().err
    assert "NOTICE" in err
    assert "not found on PATH" in err


def test_sync_failure_surfaces_and_exits(capsys):
    def run(_uv: str) -> subprocess.CompletedProcess[str]:
        return _completed(2, stderr="No solution found when resolving dependencies")

    with pytest.raises(typer.Exit) as excinfo:
        _ensure(which=lambda _tool: "/usr/bin/uv", run=run)

    assert excinfo.value.exit_code == 2  # propagates uv's own exit code
    err = capsys.readouterr().err
    assert "uv sync` failed" in err
    assert "No solution found when resolving dependencies" in err


def test_sync_failure_falls_back_to_stdout(capsys):
    # When uv wrote its diagnosis to stdout rather than stderr, it is still surfaced.
    def run(_uv: str) -> subprocess.CompletedProcess[str]:
        return _completed(1, stdout="lockfile mismatch")

    with pytest.raises(typer.Exit) as excinfo:
        _ensure(which=lambda _tool: "/usr/bin/uv", run=run)

    assert excinfo.value.exit_code == 1
    assert "lockfile mismatch" in capsys.readouterr().err


def test_default_run_seam_targets_uv_sync(monkeypatch):
    # The default subprocess seam is a real call (excluded from coverage); assert its
    # shape by confirming the helper hands `<uv> sync` to subprocess.run in repo_root.
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN003 - test double for subprocess.run
        seen.append(argv)
        return _completed(0)

    monkeypatch.setattr(venvsync.subprocess, "run", fake_run)
    result = venvsync._uv_sync("/opt/uv")

    assert seen == [["/opt/uv", "sync"]]
    assert result.returncode == 0
