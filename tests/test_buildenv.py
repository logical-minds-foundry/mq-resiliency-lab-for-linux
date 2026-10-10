from __future__ import annotations

from pathlib import Path

import pytest

from mqlab import buildenv as b


def _git(mapping):
    """Fake git runner: maps the subcommand tail -> output."""

    def run(args: list[str]) -> str:
        return mapping[" ".join(args[1:])]  # drop "git"

    return run


def test_real_git_runs_relative_to_repo_root(monkeypatch):
    # b._git must resolve git against repo_root(), NOT the process cwd — otherwise mqlab
    # crashes as a root systemd service with cwd=/ ("not a git repository", #989).
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        captured["stdin"] = kwargs.get("stdin")

        class _Result:
            stdout = "  /repo/.git  \n"

        return _Result()

    monkeypatch.setattr(b, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    assert b._git(["git", "rev-parse", "--git-dir"]) == "/repo/.git"
    assert captured["cwd"] == Path("/repo")
    assert captured["args"] == ["git", "rev-parse", "--git-dir"]
    assert captured["stdin"] is b.subprocess.DEVNULL  # own stdin, not mqlab's fd 0 (#1420)


_DUBIOUS = (
    "fatal: detected dubious ownership in repository at '/repo'\n"
    "To add an exception for this directory, call:\n"
)


def test_real_git_failure_surfaces_command_exit_cwd_and_stderr(monkeypatch):
    # #1261: a git failure must say WHY — git's own stderr — not just "exit 128".
    def fake_run(args, **kwargs):
        raise b.subprocess.CalledProcessError(128, args, output="", stderr=_DUBIOUS)

    monkeypatch.setattr(b, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    with pytest.raises(b.BuildEnvError) as err:
        b._git(["git", "rev-parse", "--git-dir"])
    msg = str(err.value)
    assert "git failed (exit 128): git rev-parse --git-dir" in msg
    assert "cwd: /repo" in msg
    assert "stderr: fatal: detected dubious ownership in repository at '/repo'" in msg
    assert isinstance(err.value.__cause__, b.subprocess.CalledProcessError)


def test_real_git_failure_with_empty_stderr_says_so(monkeypatch):
    def fake_run(args, **kwargs):
        raise b.subprocess.CalledProcessError(128, args, output="", stderr=None)

    monkeypatch.setattr(b, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    with pytest.raises(b.BuildEnvError, match=r"stderr: \(no stderr\)"):
        b._git(["git", "rev-parse", "--git-common-dir"])


def test_real_git_unrunnable_raises_build_env_error(monkeypatch):
    def fake_run(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(b, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(b.subprocess, "run", fake_run)
    with pytest.raises(b.BuildEnvError) as err:
        b._git(["git", "rev-parse", "--git-dir"])
    assert "git failed (could not run): git rev-parse --git-dir" in str(err.value)
    assert "No such file or directory" in str(err.value)


def test_ensure_propagates_git_failure_diagnosis(tmp_path):
    def failing(args: list[str]) -> str:
        raise b.BuildEnvError(
            b.git_failure_message(args, tmp_path, exit_code=128, stderr="fatal: boom")
        )

    with pytest.raises(b.BuildEnvError, match="stderr: fatal: boom"):
        b.ensure(tmp_path, run=failing)


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


# --- cold-boot stamp (write-once, epic .github#91 T6) ---
def test_ensure_writes_cold_boot_stamp_when_absent(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    b.ensure(main, run=_real_git(main, main), now_iso=lambda: "2026-01-01T00:00:00+00:00")
    stamp = main / "build" / "state" / b.COLD_BOOT_STAMP
    assert stamp.read_text() == "2026-01-01T00:00:00+00:00"


def test_ensure_does_not_overwrite_existing_stamp(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    b.ensure(main, run=_real_git(main, main), now_iso=lambda: "2026-01-01T00:00:00+00:00")
    # a second ensure (later clock) must NOT overwrite the write-once stamp
    b.ensure(main, run=_real_git(main, main), now_iso=lambda: "2026-06-01T00:00:00+00:00")
    stamp = main / "build" / "state" / b.COLD_BOOT_STAMP
    assert stamp.read_text() == "2026-01-01T00:00:00+00:00"


def test_ensure_stamps_shared_state_from_a_worktree(tmp_path):
    # in a worktree the stamp lands in the MAIN checkout's shared state bucket
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    b.ensure(wt, run=_real_git(wt, main), now_iso=lambda: "2026-01-01T00:00:00+00:00")
    assert (main / "build" / "state" / b.COLD_BOOT_STAMP).read_text() == "2026-01-01T00:00:00+00:00"


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
    b.ensure(main, run=run)
    b.migrate(main)
    assert (build / "cache" / "mq").is_dir()
    assert (build / "work" / "inventory.ini").exists()
    assert (build / "state" / "snapshots").is_dir()
    assert not (build / "mq").exists()


def test_migrate_moves_a_root_install_dvd_into_state(tmp_path):
    # The install DVD is matched by pattern (MIGRATION_GLOBS), not by a hand-written
    # versioned name, so any <name>-dvd.iso at the build/ root lands in state (#1279).
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    build = main / "build"
    build.mkdir()
    (build / "rhel-9.6-x86_64-dvd.iso").write_text("iso")
    (build / "notes.iso").write_text("x")  # not an install DVD: not migrated
    b.ensure(main, run=run)
    planned = b.migrate(main).moves
    assert (build / "state" / "rhel-9.6-x86_64-dvd.iso").read_text() == "iso"
    assert not (build / "rhel-9.6-x86_64-dvd.iso").exists()
    assert (build / "notes.iso").exists()
    assert any(src.endswith("/rhel-9.6-x86_64-dvd.iso") for src, _ in planned)


def test_migrate_dry_run_moves_nothing(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    planned = b.migrate(main, dry_run=True).moves
    assert any(src.endswith("/mq") for src, _ in planned)
    assert (main / "build" / "mq").exists()  # untouched


def test_migrate_is_idempotent(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    b.migrate(main)
    assert b.migrate(main) == b.MigrationPlan()  # nothing left to move


def test_migrate_collision_raises(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    build = main / "build"
    (build / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    (build / "cache" / "mq").mkdir(parents=True)  # destination already exists
    (build / "snapshots").mkdir()  # a clean move, blocked by the collision (all-or-nothing)
    with pytest.raises(b.BuildEnvError) as exc:
        b.migrate(main)
    assert str(exc.value) == (
        "migrate collision; nothing was moved:\n"
        f"  {build / 'cache' / 'mq'} already exists (legacy {build / 'mq'} not moved)\n"
        "Reconcile each by hand (keep the copy you want, remove the other), "
        "then re-run `mqlab build migrate`."
    )
    assert (build / "snapshots").is_dir()
    assert not (build / "state" / "snapshots").exists()


# --- vagrant dotfile relocation (#355) ---
def test_migrate_relocates_vagrant_dotfile(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "lab" / ".vagrant" / "machines").mkdir(parents=True)
    (main / "lab" / ".vagrant" / "machines" / "id").write_text("dom")
    planned = b.migrate(main).moves
    assert (main / "build" / "state" / "vagrant" / "machines" / "id").read_text() == "dom"
    assert not (main / "lab" / ".vagrant").exists()
    assert any(dst.endswith("/build/state/vagrant") for _, dst in planned)


def test_migrate_vagrant_dry_run_moves_nothing(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "lab" / ".vagrant").mkdir(parents=True)
    planned = b.migrate(main, dry_run=True).moves
    assert any(dst.endswith("/build/state/vagrant") for _, dst in planned)
    assert (main / "lab" / ".vagrant").exists()  # untouched


def test_migrate_reports_a_stale_legacy_vagrant_dotfile_and_keeps_it(tmp_path):
    # #1295: a stale lab/.vagrant beside the canonical build/state/vagrant used to abort
    # migrate. It is now reported (path + how to remove it by hand), never deleted, and
    # the rest of the migration still runs.
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "lab" / ".vagrant" / "machines").mkdir(parents=True)
    (main / "lab" / ".vagrant" / "machines" / "id").write_text("old")
    (main / "build" / "state" / "vagrant").mkdir(parents=True)
    (main / "build" / "state" / "vagrant" / "id").write_text("live")
    (main / "build" / "mq").mkdir()
    plan = b.migrate(main)
    legacy = main / "lab" / ".vagrant"
    assert plan.stale == [
        f"{legacy} is a stale legacy vagrant dotfile, ignored (the lab uses "
        f"{main / 'build' / 'state' / 'vagrant'}). Once you have confirmed nothing needs "
        f"it, remove it by hand: rm -rf {legacy}"
    ]
    assert (legacy / "machines" / "id").read_text() == "old"  # never deleted
    assert (main / "build" / "state" / "vagrant" / "id").read_text() == "live"
    assert plan.moves == [(str(main / "build" / "mq"), str(main / "build" / "cache" / "mq"))]
    assert (main / "build" / "cache" / "mq").is_dir()


# --- refs merge: legacy build/refs beside build/cache/refs (#1295) ---
def _refs_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    main = tmp_path / "main"
    legacy = main / "build" / "refs" / "ibm-docs"
    bucket = main / "build" / "cache" / "refs" / "ibm-docs"
    legacy.mkdir(parents=True)
    bucket.mkdir(parents=True)
    return main, legacy, bucket


def test_migrate_merges_legacy_refs_without_loss(tmp_path):
    main, legacy, bucket = _refs_pair(tmp_path)
    (legacy / "ibm-mq" / "9.4.x" / "only-legacy").mkdir(parents=True)
    (legacy / "ibm-mq" / "9.4.x" / "only-legacy" / "content.txt").write_text("L")
    (legacy / "ibm-mq" / "9.4.x" / "shared").mkdir(parents=True)
    (legacy / "ibm-mq" / "9.4.x" / "shared" / "content.txt").write_text("same")
    (legacy / "ibm-mq" / "9.4.x" / "shared" / "extra.txt").write_text("E")
    (bucket / "ibm-mq" / "9.4.x" / "shared").mkdir(parents=True)
    (bucket / "ibm-mq" / "9.4.x" / "shared" / "content.txt").write_text("same")
    (bucket / "ibm-mq" / "9.4.x" / "only-bucket").mkdir()
    plan = b.migrate(main)
    shared_legacy = legacy / "ibm-mq" / "9.4.x" / "shared"
    shared_bucket = bucket / "ibm-mq" / "9.4.x" / "shared"
    assert plan.duplicates == [
        (str(shared_legacy / "content.txt"), str(shared_bucket / "content.txt"))
    ]
    assert (
        str(legacy / "ibm-mq" / "9.4.x" / "only-legacy"),
        str(bucket / "ibm-mq" / "9.4.x" / "only-legacy"),
    ) in plan.moves
    assert (str(shared_legacy / "extra.txt"), str(shared_bucket / "extra.txt")) in plan.moves
    assert (bucket / "ibm-mq" / "9.4.x" / "only-legacy" / "content.txt").read_text() == "L"
    assert (shared_bucket / "extra.txt").read_text() == "E"
    assert (shared_bucket / "content.txt").read_text() == "same"
    assert (bucket / "ibm-mq" / "9.4.x" / "only-bucket").is_dir()
    assert not (main / "build" / "refs").exists()  # legacy tree fully retired
    assert b.migrate(main) == b.MigrationPlan()  # idempotent


def test_migrate_refs_merge_dry_run_changes_nothing(tmp_path):
    main, legacy, bucket = _refs_pair(tmp_path)
    (legacy / "a.txt").write_text("same")
    (bucket / "a.txt").write_text("same")
    (legacy / "b.txt").write_text("new")
    plan = b.migrate(main, dry_run=True)
    assert plan.duplicates == [(str(legacy / "a.txt"), str(bucket / "a.txt"))]
    assert plan.moves == [(str(legacy / "b.txt"), str(bucket / "b.txt"))]
    assert (legacy / "a.txt").exists()
    assert (legacy / "b.txt").exists()
    assert not (bucket / "b.txt").exists()


def test_migrate_refs_differing_conflict_refuses_and_moves_nothing(tmp_path):
    main, legacy, bucket = _refs_pair(tmp_path)
    (legacy / "page").mkdir()
    (legacy / "page" / "content.txt").write_text("9.4 copy")
    (bucket / "page").mkdir()
    (bucket / "page" / "content.txt").write_text("10.0 copy")
    (legacy / "dir-vs-file").mkdir()
    (bucket / "dir-vs-file").write_text("f")
    (legacy / "missing.txt").write_text("m")  # would move, but a conflict blocks everything
    (main / "build" / "mq").mkdir()  # an unrelated move is blocked too
    with pytest.raises(b.BuildEnvError) as exc:
        b.migrate(main)
    msg = str(exc.value)
    assert msg.startswith("migrate collision; nothing was moved:\n")
    assert f"{legacy / 'dir-vs-file'} differs from {bucket / 'dir-vs-file'}" in msg
    assert (
        f"{legacy / 'page' / 'content.txt'} differs from {bucket / 'page' / 'content.txt'}" in msg
    )
    assert msg.endswith("then re-run `mqlab build migrate`.")
    assert (legacy / "page" / "content.txt").read_text() == "9.4 copy"  # never overwritten
    assert (bucket / "page" / "content.txt").read_text() == "10.0 copy"
    assert (legacy / "missing.txt").exists()
    assert (main / "build" / "mq").is_dir()


def test_migrate_refs_symlinks_compare_by_target(tmp_path):
    main, legacy, bucket = _refs_pair(tmp_path)
    (legacy / "same-link").symlink_to("target-a")
    (bucket / "same-link").symlink_to("target-a")
    (legacy / "diff-link").symlink_to("target-a")
    (bucket / "diff-link").symlink_to("target-b")
    (legacy / "link-vs-file").symlink_to("target-a")
    (bucket / "link-vs-file").write_text("f")
    (legacy / "dangling").symlink_to("nowhere")  # absent in the bucket: moved as a link
    with pytest.raises(b.BuildEnvError) as exc:
        b.migrate(main)
    msg = str(exc.value)
    assert f"{legacy / 'diff-link'} differs from {bucket / 'diff-link'}" in msg
    assert f"{legacy / 'link-vs-file'} differs from {bucket / 'link-vs-file'}" in msg
    assert "same-link differs" not in msg
    (bucket / "diff-link").unlink()
    (bucket / "link-vs-file").unlink()
    plan = b.migrate(main)
    assert plan.duplicates == [(str(legacy / "same-link"), str(bucket / "same-link"))]
    assert (bucket / "dangling").readlink() == Path("nowhere")
    assert (bucket / "diff-link").readlink() == Path("target-a")


def test_migrate_refs_into_a_dangling_bucket_symlink_is_a_conflict(tmp_path):
    # A destination that is a (dangling) symlink exists for merge purposes: never
    # clobber it with a rename.
    main, legacy, bucket = _refs_pair(tmp_path)
    (legacy / "x").write_text("x")
    (bucket / "x").symlink_to("nowhere")
    with pytest.raises(b.BuildEnvError, match="differs from"):
        b.migrate(main)
    assert (bucket / "x").is_symlink()


def test_migrate_refs_not_merged_when_legacy_is_a_file(tmp_path):
    # MERGEABLE only applies dir-into-dir; any other shape is a plain collision.
    main = tmp_path / "main"
    (main / "build" / "cache" / "refs").mkdir(parents=True)
    (main / "build" / "refs").write_text("odd")
    with pytest.raises(b.BuildEnvError, match="already exists"):
        b.migrate(main)
    assert (main / "build" / "refs").read_text() == "odd"


def test_migrate_vagrant_skips_a_symlink(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "elsewhere").mkdir()
    (main / "lab").mkdir(parents=True)
    (main / "lab" / ".vagrant").symlink_to(main / "elsewhere")  # already a symlink -> skip
    assert b.migrate_vagrant_dotfile(main) is None


def test_ensure_auto_heals_vagrant_dotfile(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "lab" / ".vagrant" / "machines").mkdir(parents=True)
    b.ensure(main, run=_real_git(main, main))  # ensure relocates it
    assert (main / "build" / "state" / "vagrant" / "machines").is_dir()
    assert not (main / "lab" / ".vagrant").exists()


def test_ensure_leaves_existing_vagrant_dotfile_untouched(tmp_path):
    # auto-heal path with a destination already present: no move, no raise (non-strict).
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / "lab" / ".vagrant").mkdir(parents=True)
    (main / "build" / "state" / "vagrant").mkdir(parents=True)
    b.ensure(main, run=_real_git(main, main))  # must not raise
    assert (main / "lab" / ".vagrant").exists()  # left in place
