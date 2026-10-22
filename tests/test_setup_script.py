from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from mqlab import launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP = REPO_ROOT / "scripts" / "setup"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def test_setup_script_exists_and_is_executable() -> None:
    assert SETUP.is_file(), "scripts/setup is missing"
    assert SETUP.stat().st_mode & stat.S_IXUSR, "scripts/setup is not executable"


def test_setup_script_is_valid_bash() -> None:
    # `bash -n` parses without executing — catches syntax errors.
    result = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_script_is_strict_and_points_at_next_command() -> None:
    text = SETUP.read_text()
    assert "set -euo pipefail" in text, "script must fail loud"
    assert "uv sync" in text, "script must materialize the environment"
    assert "mqlab bootstrap" in text, "script must point the user at the bring-up command"
    assert "mqlab doctor" in text, "script must point the user at the pre-flight gate"


def test_setup_script_hardcodes_no_python_version() -> None:
    # The version follows Vergil's window (#1681): setup reads the floor from
    # pyproject.toml instead of naming one, and carries no stale 3.12 (#1063).
    text = SETUP.read_text()
    assert not re.search(r"\b3\.1\d\b", text), "setup hardcodes a Python version"


def _run_setup_with_fake_uv(tmp_path: Path, pyproject: str) -> tuple[int, str, list[str]]:
    """Run a copy of scripts/setup against `pyproject`, with a fake `uv` on PATH.

    The fake `uv` logs every call and reports no interpreter found, so the
    script takes its install path. Returns (rc, stderr, logged uv calls).
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SETUP, repo / "scripts" / "setup")
    (repo / "pyproject.toml").write_text(pyproject)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "uv.log"
    fake_uv = bindir / "uv"
    fake_uv.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$UV_LOG"\n[ "$1 $2" = "python find" ] && exit 1\nexit 0\n'
    )
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "UV_LOG": str(log)}
    # Run from an unrelated cwd: setup must find the repo root itself.
    result = subprocess.run(
        [str(repo / "scripts" / "setup")], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result.returncode, result.stderr, calls


def test_setup_script_provisions_the_requires_python_floor(tmp_path: Path) -> None:
    # On a miss, setup installs a managed interpreter at the requires-python
    # floor of the real pyproject.toml — the same version as launcher.MIN_PYTHON.
    floor = ".".join(str(part) for part in launcher.MIN_PYTHON)
    rc, stderr, calls = _run_setup_with_fake_uv(tmp_path, PYPROJECT.read_text())
    assert rc == 0, stderr
    assert calls == [f"python find {floor}", f"python install {floor}", "sync"]


def test_setup_script_fails_loud_without_a_floor(tmp_path: Path) -> None:
    rc, stderr, calls = _run_setup_with_fake_uv(tmp_path, '[project]\nname = "x"\n')
    assert rc != 0
    assert "cannot read the Python floor" in stderr
    assert calls == []
