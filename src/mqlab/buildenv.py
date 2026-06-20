"""Git-aware build/ bucket resolution and lifecycle (#286).

The one place that knows where the *main* checkout's build/ lives (so shared
buckets resolve there) and how to wire/clean/migrate the four buckets. paths.py
stays pure (it relies on the cache/state symlinks this module lays); the git
subprocess work is isolated here and injected in tests.
"""

from __future__ import annotations

import shutil
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


def _git(args: list[str]) -> str:  # pragma: no cover - real git subprocess (injected in tests)
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


def _make_bucket(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitkeep").touch()


def ensure(repo: Path, *, run: Callable[[list[str]], str] = _git) -> None:
    """Wire repo/build/* . In a worktree, cache/ and state/ symlink back to main;
    work/ and temp/ are always real local dirs. Idempotent; fails loud if a worktree
    already holds a real (non-symlink) shared bucket."""
    worktree = is_worktree(run=run)
    main = main_build_root(repo, run=run) if worktree else repo / "build"
    local_build = repo / "build"
    local_build.mkdir(parents=True, exist_ok=True)

    for bucket in LOCAL:
        _make_bucket(local_build / bucket)

    for bucket in SHARED:
        target = local_build / bucket
        if not worktree:
            _make_bucket(target)
            continue
        _make_bucket(main / bucket)  # ensure main owns the real dir
        if target.is_symlink():
            continue  # idempotent: trust an existing symlink
        if target.exists():
            raise BuildEnvError(
                f"worktree has a real {bucket!r} dir at {target}; expected a symlink to "
                f"{main / bucket}. Move/remove it, then re-run `mqlab build ensure`."
            )
        target.symlink_to(main / bucket)


def _reset_bucket(path: Path) -> None:
    if path.is_symlink():
        path = path.resolve()  # worktree shared bucket: clear the target, keep the link
    if path.is_dir():
        shutil.rmtree(path)
    _make_bucket(path)


def clean(
    repo: Path,
    *,
    drop_cache: bool = False,
    drop_state: bool = False,
    run: Callable[[list[str]], str] = _git,
) -> list[str]:
    """Nuke work/+temp/ (+ any stray non-bucket entries at build/ root). --cache also
    drops re-fetchable downloads; --state the irreplaceable lab state (caller guards)."""
    build = repo / "build"
    removed: list[str] = []
    targets = ["work", "temp"]
    if drop_cache:
        targets.append("cache")
    if drop_state:
        targets.append("state")
    for bucket in targets:
        _reset_bucket(build / bucket)
        removed.append(bucket)
    for entry in build.iterdir():  # stray non-bucket entries are disposable (D1)
        if entry.name in BUCKETS or entry.name == ".gitkeep":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed.append(entry.name)
    return removed


# old build/ top-level name -> destination bucket (spec §6).
MIGRATION = {
    "mq": "cache",
    "refs": "cache",
    "ansible_collections": "cache",
    "rhel-9.6-x86_64-dvd.iso": "state",
    "snapshots": "state",
    "boxes": "state",
    "rhel96-box": "state",
    "secrets": "state",
    "rhel-ha": "state",  # operator-curated HA package repo (entitlement-gated on RHEL)
    "fence_key": "state",
    "fence_key.pub": "state",
    "runs": "state",
    "reports": "state",
    "dr-runs": "state",
    "inventory.ini": "work",
    "lab": "work",
    "box-versions.json": "work",
    "versions.json": "work",
    "grafana": "work",
    "prometheus": "work",
    "obs": "work",
    "salt": "work",
}


def migrate(
    repo: Path, *, dry_run: bool = False, run: Callable[[list[str]], str] = _git
) -> list[tuple[str, str]]:
    """Move existing top-level build/ entries into their bucket (rename = instant, even
    for the 22G snapshots). Idempotent; raises on a destination collision."""
    build = repo / "build"
    planned: list[tuple[str, str]] = []
    for name, bucket in MIGRATION.items():
        src = build / name
        if not src.exists():
            continue
        dst = build / bucket / name
        if dst.exists():
            raise BuildEnvError(f"migrate collision: {dst} already exists; resolve by hand")
        planned.append((str(src), str(dst)))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
    return planned
