from __future__ import annotations

from pathlib import Path

import pytest

from mqlab import buildenv as b


def _git(mapping):
    """Fake git runner: maps the subcommand tail -> output."""

    def run(args: list[str]) -> str:
        return mapping[" ".join(args[1:])]  # drop "git"

    return run


def test_is_worktree_true_when_dirs_differ():
    run = _git(
        {"rev-parse --git-dir": "/repo/.git/worktrees/wt", "rev-parse --git-common-dir": "/repo/.git"}
    )
    assert b.is_worktree(run=run) is True


def test_is_worktree_false_in_main():
    run = _git({"rev-parse --git-dir": "/repo/.git", "rev-parse --git-common-dir": "/repo/.git"})
    assert b.is_worktree(run=run) is False


def test_main_build_root_is_common_dir_parent_build():
    run = _git({"rev-parse --git-common-dir": "/repo/.git"})
    assert b.main_build_root(Path("/repo/.worktrees/wt"), run=run) == Path("/repo/build")


def test_main_build_root_relative_common_dir():
    # from the main worktree git returns a relative ".git" -> resolve against repo
    run = _git({"rev-parse --git-common-dir": ".git"})
    assert b.main_build_root(Path("/repo"), run=run) == Path("/repo/build")


def test_bucket_path_shared_resolves_to_main():
    run = _git(
        {"rev-parse --git-dir": "/repo/.git/worktrees/wt", "rev-parse --git-common-dir": "/repo/.git"}
    )
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("cache", wt, run=run) == Path("/repo/build/cache")
    assert b.bucket_path("state", wt, run=run) == Path("/repo/build/state")


def test_bucket_path_local_for_work_and_temp():
    run = _git(
        {"rev-parse --git-dir": "/repo/.worktrees/wt/.git", "rev-parse --git-common-dir": "/repo/.git"}
    )
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("work", wt, run=run) == wt / "build" / "work"
    assert b.bucket_path("temp", wt, run=run) == wt / "build" / "temp"


def test_bucket_path_rejects_unknown_bucket():
    with pytest.raises(b.BuildEnvError, match="unknown bucket"):
        b.bucket_path("nope", Path("/repo"), run=_git({}))
