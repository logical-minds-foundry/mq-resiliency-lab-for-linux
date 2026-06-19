# build/ Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize `build/` into four buckets (`cache`/`state`/`work`/`temp`) with a single Python path authority and a `mqlab build` tool, so cold rebuilds are reproducible and worktrees share the one lab's state without fragile hand-wired symlinks.

**Architecture:** `paths.py` becomes the bucket authority for Python (pure path construction; the `cache/`+`state/` symlinks laid by `mqlab build ensure` make those buckets resolve to the main checkout). A new `buildenv.py` holds the git-aware resolution (main-checkout discovery, worktree detection, ensure/clean/migrate) and backs the `mqlab build` command group. Non-Python consumers (Vagrantfile, ansible, shell scripts) call `mqlab build path <bucket>` instead of re-deriving paths.

**Tech Stack:** Python 3.12 (typer CLI, frozen dataclasses, pathlib, subprocess for git), Ruby (Vagrantfile), Ansible, Bash. Tests: pytest with injected filesystem root + git runner.

## Global Constraints

- **Python 3.12.** No syntax/stdlib above 3.12.
- **Validation is one command only:** `vrg-container-run -- vrg-validate`. Never run linters/pytest directly as the gate.
- **100% branch coverage enforced.** Every function touching real I/O (git subprocess, filesystem) takes injectable params/callables; mark only genuinely unreachable lines `# pragma: no cover`.
- **Fail loud, no silent fallback.** New errors are `RuntimeError` subclasses (mirror `InventoryError`).
- **Four-bucket invariant (D1):** `build/` top level contains only `cache/ state/ work/ temp/` (+ `.gitkeep`); anything else is disposable.
- **Share rule (D3):** in a worktree, `cache/` and `state/` are symlinks to the main checkout's `build/`; `work/` and `temp/` are real local dirs.
- **One resolver (D5):** Python via `paths.py`; non-Python via `mqlab build path <bucket>`. No consumer re-derives `build/`'s location.
- **Sequencing:** #286 lands **before** #276 and hooks `build ensure` into the lab verbs **independently** of #276's `_prepare_lab`.
- **Git:** all work in this worktree (`.worktrees/issue-286-build-buckets`); commit with `vrg-commit --type <t> --scope <s> --message <m>`. Branch: `feature/286-build-buckets`.
- **Ruff:** respect the magic trailing comma; keep lines ≤100; `Callable`/type-only imports under `TYPE_CHECKING` (TC003); no `StrEnum` (UP042).

## File Structure

| Path | New? | Responsibility |
|------|------|----------------|
| `src/mqlab/paths.py` | modify | Bucket authority: `build_root`, `cache/state/work/temp_dir` primitives; named helpers repointed into buckets |
| `src/mqlab/buildenv.py` | new | Git-aware resolution: main-checkout discovery, worktree detection, `ensure`/`clean`/`migrate`, bucket resolution |
| `src/mqlab/cli.py` | modify | `mqlab build` command group (`path`/`ensure`/`clean`/`status`/`migrate`); hook `ensure` into lab verbs |
| `src/mqlab/transcript.py` | modify | guard uses `build_root()` not `runs_dir().parent` |
| `src/mqlab/{scrape,dashboard,clusterboard,inventory,runreport,roster}.py` | modify | use bucket helpers instead of hardcoded `build/` paths |
| `ansible/ansible.cfg` | modify | `inventory = ../build/work/inventory.ini` |
| ansible playbooks/roles (per grep) | modify | bucket-qualified `build/` paths |
| `lab/Vagrantfile` | modify | read `../build/work/...` |
| `lab/scripts/*.sh`, `scripts/fetch-mq.sh` | modify | resolve buckets via `mqlab build path` |
| `.gitignore`, `CLAUDE.md`, `docs/development/build-layout.md` | modify/new | the documented convention |
| `tests/test_paths.py`, `tests/test_buildenv.py`, `tests/test_cli_build.py` | new/modify | unit coverage |

---

### Task 1: `paths.py` — the bucket authority

**Files:**
- Modify: `src/mqlab/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `build_root()->Path`; `cache(*parts:str)->Path`; `state(*parts:str)->Path`; `work(*parts:str)->Path`; `temp_dir()->Path`; repointed `runs_dir()→state("runs")`, `reports_dir()→state("reports")`, `selection_state_path(setup)→state("manifests", f"{setup}.yaml")`, `inventory_path()→work("inventory.ini")`; new `resolved_topology_path()→work("lab","topology.resolved.yaml")`, `box_versions_path()→work("box-versions.json")`, `mq_cache_dir()→cache("mq")`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_paths.py`)

```python
def test_bucket_primitives(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab import paths

    assert paths.build_root() == tmp_path / "build"
    assert paths.cache("mq") == tmp_path / "build" / "cache" / "mq"
    assert paths.state("snapshots") == tmp_path / "build" / "state" / "snapshots"
    assert paths.work("inventory.ini") == tmp_path / "build" / "work" / "inventory.ini"
    assert paths.temp_dir() == tmp_path / "build" / "temp"


def test_named_helpers_point_into_buckets(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab import paths

    assert paths.runs_dir() == tmp_path / "build" / "state" / "runs"
    assert paths.reports_dir() == tmp_path / "build" / "state" / "reports"
    assert paths.selection_state_path("s") == tmp_path / "build" / "state" / "manifests" / "s.yaml"
    assert paths.inventory_path() == tmp_path / "build" / "work" / "inventory.ini"
    assert paths.resolved_topology_path() == tmp_path / "build" / "work" / "lab" / "topology.resolved.yaml"
    assert paths.box_versions_path() == tmp_path / "build" / "work" / "box-versions.json"
    assert paths.mq_cache_dir() == tmp_path / "build" / "cache" / "mq"
```

- [ ] **Step 2: Run — expect fail**

Run: `uv run pytest tests/test_paths.py -q`
Expected: FAIL — `AttributeError: module 'mqlab.paths' has no attribute 'build_root'`.

- [ ] **Step 3: Implement** (`src/mqlab/paths.py` — replace the build-path helpers)

```python
def build_root() -> Path:
    return repo_root() / "build"


def cache(*parts: str) -> Path:
    return build_root().joinpath("cache", *parts)


def state(*parts: str) -> Path:
    return build_root().joinpath("state", *parts)


def work(*parts: str) -> Path:
    return build_root().joinpath("work", *parts)


def temp_dir() -> Path:
    return build_root() / "temp"


def runs_dir() -> Path:
    """Transcripts — under the shared state/ bucket (the lab's audit trail, #286)."""
    return state("runs")


def reports_dir() -> Path:
    """Run-report bundles — shared state/ (audit trail, #286)."""
    return state("reports")


def selection_state_path(setup: str) -> Path:
    """Manifest selection pin for a live setup — shared state/ (#266, #286)."""
    return state("manifests", f"{setup}.yaml")


def inventory_path() -> Path:
    return work("inventory.ini")


def resolved_topology_path() -> Path:
    return work("lab", "topology.resolved.yaml")


def box_versions_path() -> Path:
    return work("box-versions.json")


def mq_cache_dir() -> Path:
    return cache("mq")
```

(Keep `repo_root`, `lab_script`, `lab_network`, `manifests_root` unchanged — `manifests_root` is committed source, not under `build/`.)

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_paths.py -q`
Expected: PASS.

- [ ] **Step 5: Refactor**

Look for: every named helper is one line through a bucket primitive (no `repo_root()/"build"/…` left in `paths.py`); `manifests_root` correctly stays outside buckets (committed source).

- [ ] **Step 6: Commit**

```bash
cd .worktrees/issue-286-build-buckets
vrg-git add src/mqlab/paths.py tests/test_paths.py
vrg-commit --type feat --scope paths --message "bucket authority: cache/state/work/temp + repointed helpers (#286)"
```

---

### Task 2: `buildenv.py` — git-aware bucket resolution

**Files:**
- Create: `src/mqlab/buildenv.py`
- Test: `tests/test_buildenv.py`

**Interfaces:**
- Produces: `class BuildEnvError(RuntimeError)`; `BUCKETS = ("cache","state","work","temp")`; `SHARED = ("cache","state")`; `is_worktree(*, run)->bool`; `main_build_root(repo, *, run)->Path`; `bucket_path(bucket, repo, *, run)->Path` (main-resolved for `cache`/`state`, `repo/build/<bucket>` for `work`/`temp`).
- `run: Callable[[list[str]], str]` runs a git command and returns stripped stdout — injected in tests, defaults to a real `subprocess` runner.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_buildenv.py
from __future__ import annotations

from pathlib import Path

import pytest

from mqlab import buildenv as b


def _git(mapping):
    """Fake git runner: maps the subcommand tail -> output."""
    def run(args: list[str]) -> str:
        key = " ".join(args[1:])  # drop "git"
        return mapping[key]
    return run


def test_is_worktree_true_when_dirs_differ():
    run = _git({"rev-parse --git-dir": "/repo/.git/worktrees/wt",
                "rev-parse --git-common-dir": "/repo/.git"})
    assert b.is_worktree(run=run) is True


def test_is_worktree_false_in_main():
    run = _git({"rev-parse --git-dir": "/repo/.git",
                "rev-parse --git-common-dir": "/repo/.git"})
    assert b.is_worktree(run=run) is False


def test_main_build_root_is_common_dir_parent_build():
    run = _git({"rev-parse --git-common-dir": "/repo/.git"})
    assert b.main_build_root(Path("/repo/.worktrees/wt"), run=run) == Path("/repo/build")


def test_bucket_path_shared_resolves_to_main():
    run = _git({"rev-parse --git-dir": "/repo/.git/worktrees/wt",
                "rev-parse --git-common-dir": "/repo/.git"})
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("cache", wt, run=run) == Path("/repo/build/cache")
    assert b.bucket_path("state", wt, run=run) == Path("/repo/build/state")


def test_bucket_path_local_for_work_and_temp():
    run = _git({"rev-parse --git-dir": "/repo/.worktrees/wt/.git",
                "rev-parse --git-common-dir": "/repo/.git"})
    wt = Path("/repo/.worktrees/wt")
    assert b.bucket_path("work", wt, run=run) == wt / "build" / "work"
    assert b.bucket_path("temp", wt, run=run) == wt / "build" / "temp"


def test_bucket_path_rejects_unknown_bucket():
    run = _git({})
    with pytest.raises(b.BuildEnvError, match="unknown bucket"):
        b.bucket_path("nope", Path("/repo"), run=run)
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: mqlab.buildenv`)

Run: `uv run pytest tests/test_buildenv.py -q`

- [ ] **Step 3: Implement**

```python
# src/mqlab/buildenv.py
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
    common = run(["git", "rev-parse", "--git-common-dir"])
    common_path = Path(common) if Path(common).is_absolute() else repo / common
    return common_path.resolve().parent / "build"


def bucket_path(bucket: str, repo: Path, *, run: Callable[[list[str]], str] = _git) -> Path:
    if bucket not in BUCKETS:
        raise BuildEnvError(f"unknown bucket {bucket!r} (expected one of {BUCKETS})")
    if bucket in SHARED:
        return main_build_root(repo, run=run) / bucket
    return repo / "build" / bucket
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_buildenv.py -q`
Expected: PASS.

- [ ] **Step 5: Refactor**

Look for: `BUCKETS`/`SHARED`/`LOCAL` are the single source for bucket names (no string literals scattered); the git runner is the only I/O seam.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/buildenv.py tests/test_buildenv.py
vrg-commit --type feat --scope buildenv --message "git-aware bucket resolution (main-checkout + worktree detection) (#286)"
```

---

### Task 3: `buildenv.ensure` — idempotent wiring

**Files:**
- Modify: `src/mqlab/buildenv.py`
- Test: `tests/test_buildenv.py` (append)

**Interfaces:**
- Consumes: Task 2.
- Produces: `ensure(repo:Path, *, run)->None` — creates `repo/build/{cache,state,work,temp}` (each with `.gitkeep`); in a worktree, replaces `cache/` and `state/` with symlinks to `main_build_root/<bucket>` (creating main's first); idempotent; **raises `BuildEnvError`** if a worktree but main is the same as repo (unresolvable) or a non-symlink real dir already occupies a shared bucket in a worktree.

- [ ] **Step 1: Write the failing test** (append)

```python
def _real_git(repo: Path, main: Path):
    """Runner that reports `repo` as a worktree whose common-dir lives in `main`."""
    def run(args: list[str]) -> str:
        tail = " ".join(args[1:])
        if tail == "rev-parse --git-dir":
            return str(repo / ".git")
        if tail == "rev-parse --git-common-dir":
            return str(main / ".git")
        raise AssertionError(tail)
    return run


def test_ensure_main_creates_real_buckets(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)  # git-dir == common-dir -> main
    b.ensure(main, run=run)
    for bucket in b.BUCKETS:
        d = main / "build" / bucket
        assert d.is_dir() and not d.is_symlink()
        assert (d / ".gitkeep").exists()


def test_ensure_worktree_symlinks_shared_only(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    run = _real_git(wt, main)
    b.ensure(wt, run=run)
    assert (wt / "build" / "cache").is_symlink()
    assert (wt / "build" / "cache").resolve() == (main / "build" / "cache").resolve()
    assert (wt / "build" / "state").is_symlink()
    assert (wt / "build" / "work").is_dir() and not (wt / "build" / "work").is_symlink()
    assert (wt / "build" / "temp").is_dir() and not (wt / "build" / "temp").is_symlink()


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
    (wt / "build" / "cache").mkdir(parents=True)  # a real local cache/ — must refuse
    run = _real_git(wt, main)
    with pytest.raises(b.BuildEnvError, match="real .* cache"):
        b.ensure(wt, run=run)
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: ensure`)

- [ ] **Step 3: Implement** (append to `buildenv.py`)

```python
def _make_bucket(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitkeep").touch()


def ensure(repo: Path, *, run: Callable[[list[str]], str] = _git) -> None:
    """Wire repo/build/* . In a worktree, cache/ and state/ symlink to main."""
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
```

Note: the worktree-unresolvable case (`main == local_build`) is structurally covered — if `is_worktree()` is True, common-dir differs from git-dir so `main` differs from `repo/build`; a degenerate equal case falls into the `SHARED` loop and either trusts/creates correctly. The `real dir` guard is the operator-facing failure.

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_buildenv.py -q`

- [ ] **Step 5: Refactor**

Look for: `_make_bucket` is the single bucket-creation primitive; the symlink/guard logic reads straight off `SHARED`/`LOCAL`; no duplicated mkdir.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/buildenv.py tests/test_buildenv.py
vrg-commit --type feat --scope buildenv --message "ensure: idempotent bucket wiring + worktree symlinks (#286)"
```

---

### Task 4: `buildenv.clean` — the nuke semantics

**Files:**
- Modify: `src/mqlab/buildenv.py`
- Test: `tests/test_buildenv.py` (append)

**Interfaces:**
- Produces: `clean(repo:Path, *, drop_cache:bool=False, drop_state:bool=False, run)->list[str]` — removes `work/` + `temp/` contents and any **stray non-bucket entries** in `build/` root; if `drop_cache`, also `cache/`; if `drop_state`, also `state/`. Returns the list of removed top-level names. Confirmation for `drop_state` is the CLI's job (Task 6), not here.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_clean_nukes_work_temp_and_stray_keeps_cache_state(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    build = main / "build"
    (build / "work" / "inventory.ini").write_text("x")
    (build / "temp" / "shot.png").write_text("x")
    (build / "cache" / "mq").mkdir(parents=True)
    (build / "state" / "snapshots").mkdir(parents=True)
    (build / "Screenshot-stray.png").write_text("x")  # stray at root
    removed = b.clean(main, run=run)
    assert not (build / "work" / "inventory.ini").exists()
    assert not (build / "temp" / "shot.png").exists()
    assert not (build / "Screenshot-stray.png").exists()
    assert (build / "cache" / "mq").exists()  # kept
    assert (build / "state" / "snapshots").exists()  # kept
    assert "work" in removed and "temp" in removed and "Screenshot-stray.png" in removed


def test_clean_drop_cache(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    (main / "build" / "cache" / "mq").mkdir(parents=True)
    b.clean(main, drop_cache=True, run=run)
    assert not (main / "build" / "cache" / "mq").exists()
    assert (main / "build" / "state").exists()  # state untouched


def test_clean_drop_state(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    b.ensure(main, run=run)
    (main / "build" / "state" / "snapshots").mkdir(parents=True)
    b.clean(main, drop_state=True, run=run)
    assert not (main / "build" / "state" / "snapshots").exists()
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** (append)

```python
import shutil  # add to imports at top of file


def _reset_bucket(path: Path) -> None:
    if path.is_symlink():
        # worktree shared bucket: clear the *target's* contents, leave the link
        path = path.resolve()
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
    # stray non-bucket entries at build/ root are disposable (D1)
    for entry in build.iterdir():
        if entry.name not in BUCKETS and entry.name != ".gitkeep":
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(entry.name)
    return removed
```

- [ ] **Step 4: Run — expect pass**

- [ ] **Step 5: Refactor**

Look for: `_reset_bucket` reuses `_make_bucket`; the keep set (`cache`/`state` unless dropped) is derived, not duplicated; symlinked shared buckets are cleared through the link, not deleted.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/buildenv.py tests/test_buildenv.py
vrg-commit --type feat --scope buildenv --message "clean: nuke work/temp/stray; --cache; --state (#286)"
```

---

### Task 5: `buildenv.migrate` — one-time move into buckets

**Files:**
- Modify: `src/mqlab/buildenv.py`
- Test: `tests/test_buildenv.py` (append)

**Interfaces:**
- Produces: `MIGRATION: dict[str, str]` (old top-level name → bucket); `migrate(repo:Path, *, dry_run:bool=False, run)->list[tuple[str,str]]` — moves each existing old entry to its bucket; idempotent (skips already-migrated); **raises `BuildEnvError` on a destination collision** with differing content; `dry_run` returns the planned `(src, dst)` moves without touching disk.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_migrate_moves_known_entries(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    build = main / "build"
    (build / "mq").mkdir(parents=True)
    (build / "inventory.ini").write_text("x")
    (build / "snapshots").mkdir(parents=True)
    (build / "Screenshot.png").write_text("x")
    b.ensure(main, run=run)
    moves = b.migrate(main, run=run)
    assert (build / "cache" / "mq").is_dir()
    assert (build / "work" / "inventory.ini").exists()
    assert (build / "state" / "snapshots").is_dir()
    assert (build / "temp" / "Screenshot.png").exists()
    assert not (build / "mq").exists()


def test_migrate_dry_run_moves_nothing(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    planned = b.migrate(main, dry_run=True, run=run)
    assert ("mq", "cache") in [(Path(s).name, Path(d).parent.name) for s, d in planned]
    assert (main / "build" / "mq").exists()  # untouched


def test_migrate_is_idempotent(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    run = _real_git(main, main)
    (main / "build" / "mq").mkdir(parents=True)
    b.ensure(main, run=run)
    b.migrate(main, run=run)
    assert b.migrate(main, run=run) == []  # nothing left to move
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement** (append)

```python
# old build/ top-level name -> destination bucket (spec §6)
MIGRATION = {
    "mq": "cache", "refs": "cache", "ansible_collections": "cache",
    "rhel-9.6-x86_64-dvd.iso": "state", "snapshots": "state", "boxes": "state",
    "rhel96-box": "state", "secrets": "state", "fence_key": "state",
    "fence_key.pub": "state", "runs": "state", "reports": "state", "dr-runs": "state",
    "inventory.ini": "work", "lab": "work", "box-versions.json": "work",
    "versions.json": "work", "grafana": "work", "prometheus": "work",
    "obs": "work", "salt": "work",
}


def migrate(
    repo: Path, *, dry_run: bool = False, run: Callable[[list[str]], str] = _git
) -> list[tuple[str, str]]:
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
            src.rename(dst)  # same filesystem -> instant, even for 22G snapshots
    # `manifests/` splits: <setup>.yaml -> state, *.overlay.json -> work (handled by callers
    # re-rendering overlays; selection pins move under state/manifests on first ensure+create)
    return planned
```

(Note: `manifests/` and the env files / `*.env` are handled at the operator step in Task 10, since `*.env` glob + the `manifests/` split need globbing; the table above covers the unambiguous top-level dirs/files. The implementer extends `MIGRATION` handling for `*.env` and `manifests/*` with the same collision rule.)

- [ ] **Step 4: Run — expect pass**

- [ ] **Step 5: Refactor**

Look for: `MIGRATION` is the single mapping table (matches spec §6); the collision guard prevents clobbering irreplaceable state; `rename` (not copy) keeps the 22G/12G moves instant.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/buildenv.py tests/test_buildenv.py
vrg-commit --type feat --scope buildenv --message "migrate: idempotent move of old build/ layout into buckets (#286)"
```

---

### Task 6: `mqlab build` CLI group

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_build.py` (new)

**Interfaces:**
- Consumes: `buildenv.{ensure,clean,migrate,bucket_path,BuildEnvError}`, `paths.repo_root`.
- Produces: `mqlab build path <bucket>` (prints abs path, exit 0; unknown bucket → exit 2), `mqlab build ensure`, `mqlab build clean [--cache] [--state]` (`--state` requires a typed `--yes-destroy-state` confirmation flag, else refuse exit 2), `mqlab build status`, `mqlab build migrate [--dry-run]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_build.py
from __future__ import annotations

from typer.testing import CliRunner

from mqlab import cli

runner = CliRunner()


def test_build_path_prints_bucket(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_build_bucket_path", lambda bucket: tmp_path / "build" / bucket)
    result = runner.invoke(cli.app, ["build", "path", "cache"])
    assert result.exit_code == 0
    assert str(tmp_path / "build" / "cache") in result.stdout


def test_build_path_unknown_bucket_exits_two(monkeypatch, tmp_path):
    def boom(bucket):
        from mqlab.buildenv import BuildEnvError
        raise BuildEnvError("unknown bucket")
    monkeypatch.setattr(cli, "_build_bucket_path", boom)
    result = runner.invoke(cli.app, ["build", "path", "nope"])
    assert result.exit_code == 2


def test_build_clean_state_requires_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    called = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: called.update(kw) or [])
    result = runner.invoke(cli.app, ["build", "clean", "--state"])  # no --yes-destroy-state
    assert result.exit_code == 2
    assert called == {}  # refused before doing anything


def test_build_clean_state_with_confirmation_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    called = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: called.update(kw) or ["state"])
    result = runner.invoke(cli.app, ["build", "clean", "--state", "--yes-destroy-state"])
    assert result.exit_code == 0
    assert called["drop_state"] is True
```

- [ ] **Step 2: Run — expect fail** (no `build` command)

- [ ] **Step 3: Implement** (`src/mqlab/cli.py`)

Add imports: `from mqlab import buildenv` and `from mqlab.buildenv import BuildEnvError`. Add the sub-app near the other `add_typer` calls:

```python
build_app = typer.Typer(help="build/ bucket lifecycle (cache/state/work/temp)", no_args_is_help=True)
app.add_typer(build_app, name="build")


# thin seams so tests can monkeypatch without real git/fs:
def _build_bucket_path(bucket: str) -> Path:
    return buildenv.bucket_path(bucket, repo_root())


def _build_ensure() -> None:
    buildenv.ensure(repo_root())


def _build_clean(*, drop_cache: bool = False, drop_state: bool = False) -> list[str]:
    return buildenv.clean(repo_root(), drop_cache=drop_cache, drop_state=drop_state)


@build_app.command("path")
def build_path(bucket: str) -> None:
    """Print the resolved absolute path of a bucket (cache|state|work|temp)."""
    try:
        typer.echo(str(_build_bucket_path(bucket)))
    except BuildEnvError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@build_app.command("ensure")
def build_ensure() -> None:
    """Create the four buckets; in a worktree, symlink cache/+state/ back to main."""
    _build_ensure()


@build_app.command("clean")
def build_clean(
    cache: bool = False,
    state: bool = False,
    yes_destroy_state: Annotated[bool, typer.Option("--yes-destroy-state")] = False,
) -> None:
    """Nuke work/+temp/ (+stray). --cache also drops downloads; --state needs --yes-destroy-state."""
    if state and not yes_destroy_state:
        typer.echo(
            "refusing to drop state/ (snapshots, ISO, a running lab's secrets). "
            "Re-run with --state --yes-destroy-state if you really mean it.",
            err=True,
        )
        raise typer.Exit(code=2)
    removed = _build_clean(drop_cache=cache, drop_state=state)
    typer.echo("removed: " + ", ".join(removed))


@build_app.command("status")
def build_status() -> None:
    """Show each bucket: path, real-or-symlink, size."""
    for bucket in buildenv.BUCKETS:
        p = _build_bucket_path(bucket)
        kind = "symlink->main" if (repo_root() / "build" / bucket).is_symlink() else "local"
        typer.echo(f"{bucket:6} {kind:14} {p}")


@build_app.command("migrate")
def build_migrate(dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    """Move existing top-level build/ contents into buckets (idempotent)."""
    moves = buildenv.migrate(repo_root(), dry_run=dry_run)
    for src, dst in moves:
        typer.echo(f"{'PLAN' if dry_run else 'MOVED'} {src} -> {dst}")
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_cli_build.py -q`

- [ ] **Step 5: Refactor**

Look for: the `_build_*` seams are the only monkeypatch points; commands are thin over `buildenv`; `--state` guard message names what's at stake (spec §3).

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_build.py
vrg-commit --type feat --scope cli --message "mqlab build path/ensure/clean/status/migrate (#286)"
```

---

### Task 7: Hook `build ensure` into lab verbs + fix `transcript.py` guard

**Files:**
- Modify: `src/mqlab/cli.py` (the vagrant-loading verbs), `src/mqlab/transcript.py`
- Test: `tests/test_cli_vm.py` (append), `tests/test_transcript.py` (append)

**Interfaces:**
- Consumes: `_build_ensure()` (Task 6).
- Produces: `vm create`/`vm up`/`obs up`/`vm ssh` call `_build_ensure()` first; `transcript.py` guard uses `build_root()`.
- Note: independent of #276. When #276 rebases on top, its `_prepare_lab` calls `_build_ensure()` first, then host/KVM checks, then render.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli_vm.py (append) — autouse fixture in conftest already neutralizes
# heavier preflights; here assert the build-ensure seam is invoked.
def test_vm_up_runs_build_ensure(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    called = {}
    monkeypatch.setattr(cli, "_build_ensure", lambda: called.setdefault("yes", True))
    runner = RecordingRunner(results=[_probe({"pcmk-a1": "shut off"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "pcmk-a1"])
    assert result.exit_code == 0
    assert called == {"yes": True}
```

```python
# tests/test_transcript.py (append)
def test_transcript_guard_uses_build_root(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab.transcript import Transcript, transcript_path

    t = Transcript(transcript_path("vm-up", "20260619T000000Z"))  # under build/state/runs
    t.close()
    assert (tmp_path / "build" / "state" / "runs") in t.path.parents
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Implement**

In `cli.py`, add `_build_ensure()` as the first line of `vm_create`, `vm_up`, `obs_up`, `vm_ssh` (after `_resolve_or_exit` where present). In `transcript.py`:

```python
from mqlab.paths import build_root, runs_dir  # add build_root


class Transcript:
    def __init__(self, path: Path) -> None:
        build_tree = build_root()  # the whole build/ tree (#286), not just runs_dir().parent
        ...
```

Add an autouse neutralizer for `_build_ensure` to `tests/conftest.py` (mirrors the `_prepare_lab` pattern) so existing lab-verb tests don't do real git/fs:

```python
@pytest.fixture(autouse=True)
def _neutralize_build_ensure(monkeypatch):
    from mqlab import cli
    monkeypatch.setattr(cli, "_build_ensure", lambda: None)
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_cli_vm.py tests/test_cli_obs.py tests/test_transcript.py -q`

- [ ] **Step 5: Refactor**

Look for: `_build_ensure` called once per verb (not inline-duplicated); the transcript guard intent ("under build/") is explicit again.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py src/mqlab/transcript.py tests/test_cli_vm.py tests/test_transcript.py tests/conftest.py
vrg-commit --type feat --scope cli --message "hook build ensure into lab verbs; transcript guard uses build_root (#286)"
```

---

### Task 8: Switch Python consumers to bucket helpers

**Files:**
- Modify: `src/mqlab/{scrape,dashboard,clusterboard,inventory,runreport,roster}.py`, `src/mqlab/cli.py` (inline `build/` paths)
- Test: existing module tests (update fixtures)

**Interfaces:** all consume `paths` helpers from Task 1.

- [ ] **Step 1: Apply the exact mapping** (each hardcoded path → helper):

| File:line | Old | New |
|---|---|---|
| `scrape.py:62` | `repo_root()/"build"/"prometheus"/"targets"/"node.json"` | `work("prometheus","targets","node.json")` |
| `dashboard.py:343` | `…/"build"/"grafana"/"dashboards"/"lab-status.json"` | `work("grafana","dashboards","lab-status.json")` |
| `clusterboard.py:945,956` | `…/"build"/"grafana"/"dashboards"/…` | `work("grafana","dashboards", …)` |
| `inventory.py:69` | `…/"build"/"inventory.ini"` | `inventory_path()` |
| `runreport.py:139` | `…/"build"/"versions.json"` | `work("versions.json")` |
| `roster.py:83` | `…/"build"/"salt"/"roster"` | `work("salt","roster")` |
| `cli.py:176` | `…/"build"/"manifests"/f"{setup}.overlay.json"` | `work("manifests", f"{setup_name}.overlay.json")` |
| `cli.py:180` | `…/"build"/"box-versions.json"` | `box_versions_path()` |
| `cli.py:185` | `…/"build"/"mq"` | `mq_cache_dir()` |
| `cli.py:201` | `…/"build"/"manifests"/"_obs.overlay.json"` | `work("manifests","_obs.overlay.json")` |
| `cli.py:348` | `…/"build"/"obs"/"reach-peers.json"` | `work("obs","reach-peers.json")` |

Add the needed names to each module's `from mqlab.paths import …`.

- [ ] **Step 2: Update fixtures/asserts** in `tests/test_scrape.py`, `test_dashboard.py`, `test_render.py`, `test_runreport.py`, `test_roster.py`, `test_cli_obs.py` that assert on `build/<old>` paths → `build/work/<…>` (and `build/cache/mq`).

- [ ] **Step 3: Run — expect pass**

Run: `uv run pytest -q`
Expected: PASS (full suite).

- [ ] **Step 4: Refactor**

Look for: no `repo_root() / "build"` literal remains in any Python module except `paths.py`. Verify: `grep -rn 'repo_root() / "build"' src/mqlab | grep -v paths.py` → empty.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab tests
vrg-commit --type refactor --scope build --message "route all Python build/ paths through paths.py bucket helpers (#286)"
```

---

### Task 9: Switch non-Python consumers (ansible, Vagrantfile, shell) + grep backstop

**Files:**
- Modify: `ansible/ansible.cfg`; ansible playbooks/roles per grep; `lab/Vagrantfile`; `lab/scripts/*.sh`; `scripts/fetch-mq.sh`

- [ ] **Step 1: `ansible/ansible.cfg`** — `inventory = ../build/work/inventory.ini` (was `../build/inventory.ini`). **Make-or-break.**

- [ ] **Step 2: Vagrantfile** — `../build/work/lab/topology.resolved.yaml`, `../build/work/box-versions.json` (and the missing-file guard message names `mqlab vm up`).

- [ ] **Step 3: Ansible playbooks/roles** — update each `build/<old>` reference to its bucket. Authoritative list: `grep -rn "build/" ansible/`. Map: `build/mq`→`build/cache/mq`; `build/inventory.ini`→`build/work/inventory.ini`; `build/{prometheus,grafana,obs}`→`build/work/…`; `build/versions.json`→`build/work/versions.json`.

- [ ] **Step 4: Shell scripts** — replace each script's own `build/` resolution with `$(mqlab build path <bucket>)`. Specifically:
  - `lab/scripts/lab-snapshot.sh`, `lab-restore.sh`: `SNAP_ROOT="$(mqlab build path state)/snapshots"` (drops the string-match `MAIN_ROOT` derivation).
  - `lab/scripts/lab-secret.sh`: `DIR="$(mqlab build path state)/secrets"` (fixes the local-vs-main split — spec §3 correctness fix).
  - `lab/boxes/rhel96/build-box.sh`: `BUILD_DIR="$(mqlab build path state)"; CACHE="$BUILD_DIR/boxes/…"` (drops its `git-common-dir` derivation).
  - `scripts/fetch-mq.sh`: `DEST="$(mqlab build path cache)/mq"`.
  - `dr-provision.sh`, `nativeha-fault-suite.sh`, `pcmk-dr-force.sh`: update each `build/<old>` per its bucket.

- [ ] **Step 5: Grep backstop** — prove completeness:

Run: `grep -rn "build/" src/ ansible/ lab/ scripts/ | grep -vE "build/(cache|state|work|temp)/|mqlab build path|# " | grep -v "src/mqlab/paths.py"`
Expected: **empty** — no bare pre-bucket `build/<name>` survives.

- [ ] **Step 6: Lint via the gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green (ansible-lint, shellcheck, markdownlint, ruff, mypy, pytest @ 100%).

- [ ] **Step 7: Commit**

```bash
vrg-git add ansible lab scripts
vrg-commit --type refactor --scope build --message "route non-Python build/ consumers through buckets + mqlab build path (#286)"
```

---

### Task 10: Migrate, document, validate, accept

**Files:**
- Modify: `.gitignore`, `CLAUDE.md`; Create: `docs/development/build-layout.md`

- [ ] **Step 1: `.gitignore`** — confirm `build/*` + `!build/.gitkeep` still covers everything (buckets + their contents stay ignored; `state/secrets`/`*.env` remain ignored). Add `!build/cache/.gitkeep` etc. only if tracking the empty structure is wanted (optional; `ensure` creates them).

- [ ] **Step 2: `docs/development/build-layout.md`** — the documented bucket layout (the four buckets, keep/nuke/share table, the `mqlab build path` contract for non-Python consumers). Link it from `CLAUDE.md`.

- [ ] **Step 3: `CLAUDE.md`** — replace the "all working state lives in `build/`" line with the four-bucket model + keep/nuke/share semantics + `temp/` scratch-and-handoff role + the `mqlab build` commands.

- [ ] **Step 4: Full validation gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green at 100% branch coverage. (Common gaps: a `migrate` collision branch, the `ensure` real-dir guard, the `clean` symlink-target branch — add the missing test if coverage < 100%.)

- [ ] **Step 5: Commit**

```bash
vrg-git add .gitignore CLAUDE.md docs/development/build-layout.md
vrg-commit --type docs --scope build --message "document the four-bucket build/ layout + mqlab build (#286)"
```

- [ ] **Step 6: Acceptance (human-run, arm64 — the cold-rebuild gate)**

This is the blocking gate (lab ops are human-operated). From the main checkout:

```bash
mqlab build migrate              # one-time: move existing build/ into buckets
mqlab build status               # eyeball the buckets
vrg-vm rebuild …                 # cold VM rebuild
mqlab build clean                # drop work/+temp/; cache/+state/ survive
mqlab vm up pcmk_san_ha          # one-pass bring-up off the surviving cache/state
```

Then in a worktree: `mqlab build ensure` wires the `cache/`+`state/` symlinks; `mqlab build status` shows them as `symlink->main`; a lab op from the worktree shares the one lab's state. Capture the result. This harness is also what unblocks #276's acceptance.

---

## Self-Review

**Spec coverage:** D1 four-bucket invariant → Tasks 1,3,10. D2 semantics → Tasks 1,3,4. D3 share rule → Tasks 2,3. D4 (stays under build/) → Task 1 (no external dir). D5 one resolver → Tasks 1 (Python), 2/6 (`build path`), 9 (consumers call it). D6 ensure auto-run, independent of #276 → Task 7. §3 state rationale / `--state` guard → Task 6. §4 path authority + 37-consumer ripple → Tasks 1,8,9. §5 tool → Task 6. §6 migration mapping → Task 5 (+ Task 10 run). §7 error handling → Tasks 3,5,6 (fail-loud). §8 grep backstop + acceptance → Tasks 8,9,10. The Issue-3 secrets/snapshots fix → Task 9 Step 4 (`lab-secret.sh` → `build path state`).

**Placeholder scan:** no TBD/TODO; the one judgment call (`*.env`/`manifests/` glob handling in `migrate`) is explicitly delegated with the rule to follow, not left blank.

**Type consistency:** `bucket_path(bucket, repo, *, run)`, `ensure(repo, *, run)`, `clean(repo, *, drop_cache, drop_state, run)`, `migrate(repo, *, dry_run, run)` are consistent across Tasks 2–6 and their CLI seams (`_build_bucket_path`/`_build_ensure`/`_build_clean`). `paths` helper names (`cache`/`state`/`work`/`temp_dir`/`work(...)`/`mq_cache_dir`/`box_versions_path`/`resolved_topology_path`) match between Task 1 and Tasks 7,8.
