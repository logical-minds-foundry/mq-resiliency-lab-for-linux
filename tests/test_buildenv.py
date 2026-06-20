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
        {
            "rev-parse --git-dir": "/repo/.git/worktrees/wt",
            "rev-parse --git-common-dir": "/repo/.git",
        }
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
        {
            "rev-parse --git-dir": "/repo/.git/worktrees/wt",
            "rev-parse --git-common-dir": "/repo/.git",
        }
    )
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("cache", wt, run=run) == Path("/repo/build/cache")
    assert b.bucket_path("state", wt, run=run) == Path("/repo/build/state")


def test_bucket_path_local_for_work_and_temp():
    run = _git(
        {
            "rev-parse --git-dir": "/repo/.worktrees/wt/.git",
            "rev-parse --git-common-dir": "/repo/.git",
        }
    )
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("work", wt, run=run) == wt / "build" / "work"
    assert b.bucket_path("temp", wt, run=run) == wt / "build" / "temp"


def test_bucket_path_rejects_unknown_bucket():
    with pytest.raises(b.BuildEnvError, match="unknown bucket"):
        b.bucket_path("nope", Path("/repo"), run=_git({}))


def _real_git(repo: Path, main: Path):
    """Runner reporting `repo` as a worktree whose common-dir lives in `main`."""

    def run(args: list[str]) -> str:
        tail = " ".join(args[1:])
        if tail == "rev-parse --git-dir":
            return str(repo / ".git")
        if tail == "rev-parse --git-common-dir":
            return str(main / ".git")
        raise AssertionError(tail)  # pragma: no cover

    return run


# --- ensure ---
def test_ensure_main_creates_real_buckets(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    b.ensure(main, run=_real_git(main, main))
    for bucket in b.BUCKETS:
        d = main / "build" / bucket
        assert d.is_dir() and not d.is_symlink() and (d / ".gitkeep").exists()


def test_ensure_worktree_symlinks_shared_only(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    b.ensure(wt, run=_real_git(wt, main))
    assert (wt / "build" / "cache").is_symlink()
    assert (wt / "build" / "cache").resolve() == (main / "build" / "cache").resolve()
    assert (wt / "build" / "state").is_symlink()
    assert (wt / "build" / "work").is_dir() and not (wt / "build" / "work").is_symlink()
    assert (wt / "build" / "temp").is_dir()


def test_ensure_is_idempotent(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    run = _real_git(wt, main)
    b.ensure(wt, run=run)
    b.ensure(wt, run=run)  # second call must not raise or change anything
    assert (wt / "build" / "cache").is_symlink()


def test_ensure_worktree_refuses_real_dir_in_shared_bucket(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "build" / "cache").mkdir(parents=True)  # a real local cache/ -> must refuse
    with pytest.raises(b.BuildEnvError, match="real 'cache'"):
        b.ensure(wt, run=_real_git(wt, main))


# --- clean ---
def test_clean_nukes_work_temp_and_stray_keeps_cache_state(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    build = main / "build"
    (build / "work" / "inv.ini").write_text("x")
    (build / "temp" / "shot.png").write_text("x")
    (build / "cache" / "mq").mkdir(parents=True)
    (build / "state" / "snapshots").mkdir(parents=True)
    (build / "Stray.png").write_text("x")  # stray file
    (build / "straydir").mkdir()  # stray dir
    removed = b.clean(main)
    assert not (build / "work" / "inv.ini").exists()
    assert not (build / "temp" / "shot.png").exists()
    assert not (build / "Stray.png").exists() and not (build / "straydir").exists()
    assert (build / "cache" / "mq").exists() and (build / "state" / "snapshots").exists()
    assert {"work", "temp", "Stray.png", "straydir"} <= set(removed)


def test_clean_drop_cache(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    (main / "build" / "cache" / "mq").mkdir(parents=True)
    b.clean(main, drop_cache=True)
    assert not (main / "build" / "cache" / "mq").exists()
    assert (main / "build" / "state").exists()


def test_clean_recreates_missing_bucket(tmp_path):
    # clean before ensure: work/temp don't exist yet -> _reset_bucket just (re)creates them
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "build").mkdir()
    b.clean(main)
    assert (main / "build" / "work" / ".gitkeep").exists()
    assert (main / "build" / "temp" / ".gitkeep").exists()


def test_clean_drop_state(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    (main / "build" / "state" / "snapshots").mkdir(parents=True)
    b.clean(main, drop_state=True)
    assert not (main / "build" / "state" / "snapshots").exists()


def test_clean_worktree_clears_symlinked_shared_through_link(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    run = _real_git(wt, main)
    b.ensure(wt, run=run)
    (main / "build" / "cache" / "mq").mkdir(parents=True)  # content via the symlink target
    b.clean(wt, drop_cache=True)
    assert (wt / "build" / "cache").is_symlink()  # link kept
    assert not (main / "build" / "cache" / "mq").exists()  # target cleared


# --- migrate ---
def test_migrate_moves_known_entries(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    build = main / "build"
    (build / "mq").mkdir(parents=True)
    (build / "inventory.ini").write_text("x")
    (build / "snapshots").mkdir(parents=True)
    (build / "rhel-ha").mkdir(parents=True)  # operator-curated HA repo -> state (not stray)
    b.ensure(main, run=run)
    b.migrate(main)
    assert (build / "cache" / "mq").is_dir()
    assert (build / "work" / "inventory.ini").exists()
    assert (build / "state" / "snapshots").is_dir()
    assert (build / "state" / "rhel-ha").is_dir()  # entitlement-gated: never a disposable stray
    assert not (build / "mq").exists()


def test_migrate_dry_run_moves_nothing(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    planned = b.migrate(main, dry_run=True)
    assert any(src.endswith("/mq") for src, _ in planned)
    assert (main / "build" / "mq").exists()  # untouched


def test_migrate_is_idempotent(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    b.migrate(main)
    assert b.migrate(main) == []  # nothing left to move


def test_migrate_collision_raises(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    build = main / "build"
    (build / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    (build / "cache" / "mq").mkdir(parents=True)  # destination already exists
    with pytest.raises(b.BuildEnvError, match="collision"):
        b.migrate(main)
