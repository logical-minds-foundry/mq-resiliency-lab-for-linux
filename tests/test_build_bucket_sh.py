"""lab/scripts/_build-bucket.sh resolves a build/ bucket through the one authority (#1569).

Shell consumers used to derive build/<bucket> from their own location
(`$(dirname "$0")/../../build/state`). In a worktree whose shared-bucket symlinks were
not yet wired that silently created a second, worktree-local state tree. The helper
instead reads MQLAB_<BUCKET>_DIR (exported by every mqlab verb) or asks
`mqlab build path <bucket>`, and fails loud rather than create a tree of its own.

lab-secret.sh is the first consumer and is exercised end to end here from a copy of the
scripts placed in a throwaway tree, so a test can prove nothing lands beside the script.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
HELPER = _REPO / "lab" / "scripts" / "_build-bucket.sh"
LAB_SECRET = _REPO / "lab" / "scripts" / "lab-secret.sh"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A clean env: no bucket vars, and a PATH holding only a stub dir plus the basics."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("MQLAB_")}
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(exist_ok=True)
    base["PATH"] = f"{stub_bin}:/usr/bin:/bin"
    return {**base, **extra}


def _stub_mqlab(tmp_path: Path, body: str) -> None:
    stub = tmp_path / "bin" / "mqlab"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(f"#!/bin/sh\n{body}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


def _resolve(bucket: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", "-c", 'set -euo pipefail; . "$1"; mqlab_bucket "$2"', "_", str(HELPER), bucket],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_env_var_wins(tmp_path: Path):
    state = tmp_path / "main" / "build" / "state"
    state.mkdir(parents=True)
    _stub_mqlab(tmp_path, "echo SHOULD-NOT-RUN; exit 9")
    result = _resolve("state", _env(tmp_path, MQLAB_STATE_DIR=str(state)))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{state}\n"


@pytest.mark.parametrize("bucket", ["cache", "state", "work", "temp"])
def test_each_bucket_reads_its_own_env_var(tmp_path: Path, bucket: str):
    path = tmp_path / bucket
    path.mkdir()
    result = _resolve(bucket, _env(tmp_path, **{f"MQLAB_{bucket.upper()}_DIR": str(path)}))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{path}\n"


def test_falls_back_to_mqlab_build_path(tmp_path: Path):
    state = tmp_path / "main" / "build" / "state"
    state.mkdir(parents=True)
    _stub_mqlab(tmp_path, f'[ "$*" = "build path state" ] || exit 7; echo "{state}"')
    result = _resolve("state", _env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{state}\n"


def test_no_env_and_no_mqlab_fails_loud(tmp_path: Path):
    result = _resolve("state", _env(tmp_path))
    assert result.returncode == 1
    assert "MQLAB_STATE_DIR is unset and mqlab is not on PATH" in result.stderr


def test_mqlab_failure_fails_loud(tmp_path: Path):
    _stub_mqlab(tmp_path, "echo 'git failed' >&2; exit 1")
    result = _resolve("state", _env(tmp_path))
    assert result.returncode == 1
    assert "'mqlab build path state' failed" in result.stderr
    assert "git failed" in result.stderr  # mqlab's own reason is passed through


def test_relative_path_is_refused(tmp_path: Path):
    result = _resolve("state", _env(tmp_path, MQLAB_STATE_DIR="build/state"))
    assert result.returncode == 1
    assert "not an absolute path" in result.stderr


def test_missing_bucket_is_refused_not_created(tmp_path: Path):
    absent = tmp_path / "nowhere" / "build" / "state"
    result = _resolve("state", _env(tmp_path, MQLAB_STATE_DIR=str(absent)))
    assert result.returncode == 1
    assert f"{absent} does not exist" in result.stderr
    assert "mqlab build ensure" in result.stderr
    assert not absent.exists()


def test_unknown_bucket_is_a_usage_error(tmp_path: Path):
    result = _resolve("bogus", _env(tmp_path))
    assert result.returncode == 2
    assert "unknown bucket 'bogus'" in result.stderr


# --- lab-secret.sh, the bug's original site ------------------------------------------


def _fake_checkout(tmp_path: Path) -> Path:
    """A worktree-shaped copy of lab/scripts with NO build/ beside it."""
    scripts = tmp_path / "repo" / ".worktrees" / "wt" / "lab" / "scripts"
    scripts.mkdir(parents=True)
    for src in (HELPER, LAB_SECRET):
        shutil.copy2(src, scripts / src.name)
    return scripts.parents[1]


def _secret(checkout: Path, name: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(checkout / "lab" / "scripts" / "lab-secret.sh"), name],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_lab_secret_writes_into_the_shared_state_bucket(tmp_path: Path):
    checkout = _fake_checkout(tmp_path)
    state = tmp_path / "repo" / "build" / "state"
    state.mkdir(parents=True)
    env = _env(tmp_path, MQLAB_STATE_DIR=str(state))
    first = _secret(checkout, "demo_password", env)
    assert first.returncode == 0, first.stderr
    stored = state / "secrets" / "demo_password"
    assert stored.read_text() == first.stdout
    assert len(first.stdout.strip()) == 32
    assert stat.S_IMODE((state / "secrets").stat().st_mode) == 0o700
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    # Idempotent: the same value forever after.
    assert _secret(checkout, "demo_password", env).stdout == first.stdout
    # And nothing was created beside the script (the #1569 fork).
    assert not (checkout / "build").exists()


def test_lab_secret_refuses_without_a_resolvable_bucket(tmp_path: Path):
    checkout = _fake_checkout(tmp_path)
    result = _secret(checkout, "demo_password", _env(tmp_path))
    assert result.returncode == 1
    assert "MQLAB_STATE_DIR is unset" in result.stderr
    assert result.stdout == ""
    assert not (checkout / "build").exists()


def test_lab_secret_refuses_a_missing_state_bucket(tmp_path: Path):
    checkout = _fake_checkout(tmp_path)
    absent = tmp_path / "repo" / "build" / "state"
    result = _secret(checkout, "demo_password", _env(tmp_path, MQLAB_STATE_DIR=str(absent)))
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert not absent.exists()
    assert not (checkout / "build").exists()
