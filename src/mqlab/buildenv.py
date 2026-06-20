"""Git-aware build/ bucket resolution and lifecycle (#286).

The one place that knows where the *main* checkout's build/ lives (so shared
buckets resolve there) and how to wire/clean/migrate the four buckets. paths.py
stays pure (it relies on the cache/state symlinks this module lays); the git
subprocess work is isolated here and injected in tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

BUCKETS = ("cache", "state", "work", "temp")
SHARED = ("cache", "state")  # symlinked back to main in a worktree
LOCAL = ("work", "temp")  # always real, per-checkout


class BuildEnvError(RuntimeError):
    """build/ cannot be characterised or wired."""


def _git(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


def is_worktree(*, run: Callable[[list[str]], str] = _git) -> bool:
    return run(["git", "rev-parse", "--git-dir"]) != run(["git", "rev-parse", "--git-common-dir"])


def main_build_root(repo: Path, *, run: Callable[[list[str]], str] = _git) -> Path:
    """The main checkout's build/ — the common-dir's parent, regardless of worktree."""
    common = Path(run(["git", "rev-parse", "--git-common-dir"]))
    if not common.is_absolute():
        common = repo / common
    return common.resolve().parent / "build"


def bucket_path(bucket: str, repo: Path, *, run: Callable[[list[str]], str] = _git) -> Path:
    if bucket not in BUCKETS:
        raise BuildEnvError(f"unknown bucket {bucket!r} (expected one of {BUCKETS})")
    if bucket in SHARED:
        return main_build_root(repo, run=run) / bucket
    return repo / "build" / bucket
