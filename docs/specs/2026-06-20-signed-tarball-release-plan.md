# Signed Tarball Release — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish this repo as a signed, semver-tagged tarball on a GitHub Release, and give consumers a one-command lab bring-up (`mqlab bootstrap <setup>`) plus an environment-setup script (`scripts/setup`).

**Architecture:** A committed `.gitattributes export-ignore` curates the `git archive` tree; a tag-triggered GitHub Actions workflow guards the version, archives, checksums, PGP-signs, and publishes the Release. `mqlab bootstrap <setup>` is a thin sequencing wrapper over existing verbs (`net create` → `vm create` → `obs up`). The README is rewritten users-first.

**Tech Stack:** Python 3.12 + Typer (CLI), pytest (TDD), GitHub Actions (CI), `git archive`, GnuPG (signing), `gh` CLI (publish).

**Design spec:** `docs/specs/2026-06-20-signed-tarball-release-design.md` (Issue #299).

## Global Constraints

- **Python:** `requires-python >=3.12`; target `py312`.
- **Validation is one command:** `vrg-container-run -- vrg-validate`. Never run individual linters. ruff + mypy `strict` + pytest with **100% branch coverage** all gate here.
- **Tests run via** `uv run pytest` under the hood of `vrg-validate`; coverage must stay at 100% (exclusions already configured in `pyproject.toml`: `if __name__ == .__main__.`, `if TYPE_CHECKING:`, `pragma: no cover`, `: ...$`).
- **Git/GitHub local wrappers:** use `vrg-git` and `vrg-commit` (conventional commits) and `vrg-gh`. Raw `git`/`gh` are denied locally. (Inside GitHub Actions runners, raw `git`/`gpg`/`gh` are fine — the wrappers are a local policy only.)
- **Worktree:** all work happens in `.worktrees/issue-299-signed-tarball-release/` on branch `feature/299-signed-tarball-release`. Run every command from there.
- **Fail loud:** no swallowed exceptions, no silent fallbacks.
- **Tarball naming:** `mq-cluster-tooling-vX.Y.Z.tar.gz`, archived with `--prefix=mq-cluster-tooling-vX.Y.Z/`.
- **`uv` is a build tool:** `scripts/setup` may call `uv sync` (environment materialization); runtime entrypoints are invoked by bare name (`mqlab`), never `uv run`.

---

## File Structure

- **Create** `.gitattributes` — curation boundary (`export-ignore` for dev-only paths).
- **Create** `scripts/setup` — consumer environment-setup script (prereq checks → `uv sync` → print next command).
- **Modify** `src/mqlab/cli.py` — add `_bootstrap_run()` helper + `@app.command("bootstrap")`.
- **Create** `tests/test_cli_bootstrap.py` — tests for the bring-up verb.
- **Create** `tools/release_version_guard.py` — tag/pyproject/VERSION equality guard.
- **Create** `tests/test_release_version_guard.py` — guard unit tests.
- **Create** `tests/test_gitattributes_curation.py` — `git archive` inclusion/exclusion test.
- **Create** `tests/test_setup_script.py` — `scripts/setup` static checks.
- **Create** `.github/workflows/release.yml` — tag-triggered release pipeline.
- **Create** `RELEASE-KEY.asc` — public signing key (convenience copy; trust root is the fingerprint + out-of-band fetch).
- **Create** `docs/development/release-runbook.md` — operator steps for keygen + CI secrets + dry run.
- **Modify** `README.md` — rewrite users-first (Intro → Getting Started → Development).

---

## Task 1: Curation boundary — `.gitattributes`

**Files:**
- Create: `.gitattributes`
- Test: `tests/test_gitattributes_curation.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `git archive HEAD` tree that **excludes** dev-only paths and **includes** the product tree. Later tasks rely on `scripts/setup` and `RELEASE-KEY.asc` being included (asserted in their own tasks).

- [ ] **Step 1: Write the failing test**

Create `tests/test_gitattributes_curation.py`. Two layers: a **declarative** check
that always runs (reads `.gitattributes`, container-safe), and an **integration**
check that actually runs `git archive` but **skips** if git isn't usable in the
environment (the `vrg-validate` container may not resolve the worktree's gitfile;
the authoritative behavior proof is the host dry-run in Task 5). The integration
test uses `--worktree-attributes` so a not-yet-committed `.gitattributes` still
takes effect — keeping the red→green cycle honest.

```python
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

EXCLUDED = [
    ".gitattributes",
    ".github/",
    ".claude/",
    ".vergil/",
    ".worktrees/",
    ".superpowers/",
    "vergil.toml",
    "tests/",
]
INCLUDED = ["src/mqlab/", "ansible/", "lab/", "manifests/", "pyproject.toml", "VERSION"]


def test_gitattributes_declares_every_exclusion() -> None:
    """Container-safe: each dev-only path is marked export-ignore."""
    lines = GITATTRIBUTES.read_text().splitlines()
    for path in EXCLUDED:
        assert any(
            line.split()[0] == path and "export-ignore" in line
            for line in lines
            if line.strip() and not line.startswith("#")
        ), f"{path} is not marked export-ignore in .gitattributes"


def _archived_names() -> set[str]:
    """Names in `git archive` of the worktree, prefix stripped. Honors the
    working-tree .gitattributes via --worktree-attributes. Decodes the archive
    in-memory — writes no file (the build/ layout is managed via `mqlab build`,
    never hardcoded). Raises on any git failure so the caller can skip in
    git-less environments."""
    out = subprocess.run(
        ["git", "archive", "--worktree-attributes", "--prefix=pkg/", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        return {m.name.removeprefix("pkg/") for m in tar.getmembers()}


def _archive_or_skip() -> set[str]:
    try:
        return _archived_names()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"git archive unavailable in this environment: {exc}")


def test_archive_excludes_dev_only_paths() -> None:
    names = _archive_or_skip()
    for path in EXCLUDED:
        assert not any(n == path or n.startswith(path) for n in names), f"{path} leaked into archive"


def test_archive_includes_product_paths() -> None:
    names = _archive_or_skip()
    for path in INCLUDED:
        assert any(n == path or n.startswith(path) for n in names), f"{path} missing from archive"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_gitattributes_curation.py -v`
Expected: `test_gitattributes_declares_every_exclusion` FAILS (`FileNotFoundError` — no `.gitattributes` yet). The integration tests either FAIL (git available, everything leaks) or SKIP (no git).

- [ ] **Step 3: Create `.gitattributes`**

```gitattributes
# Curation boundary for the release tarball (docs/specs/2026-06-20-signed-tarball-release-design.md §4).
# `git archive` omits these paths so the published tree carries only the product,
# not developer-only scaffolding. (.git and gitignored paths like build/ are
# already excluded by git archive and need no entry here.)
.gitattributes        export-ignore
.github/              export-ignore
.claude/              export-ignore
.vergil/              export-ignore
.worktrees/           export-ignore
.superpowers/         export-ignore
vergil.toml           export-ignore
tests/                export-ignore
```

> Note on `docs/specs/`: the design (§4) flagged the internal-specs cut as judgement-heavy. For V1 we ship `docs/` whole — operator-facing docs live there and the specs are harmless reference. Revisit only if a consumer complaint warrants it. Do **not** export-ignore `docs/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_gitattributes_curation.py -v`
Expected: `test_gitattributes_declares_every_exclusion` PASSES; the two integration tests PASS (if git is usable here) or SKIP (git-less env). No failures.

- [ ] **Step 5: Commit**

```bash
vrg-git add .gitattributes tests/test_gitattributes_curation.py
vrg-commit --type feat --scope release --message "curate release tarball via .gitattributes export-ignore (#299)"
```

---

## Task 2: Lab bring-up verb — `mqlab bootstrap <setup>`

**Files:**
- Modify: `src/mqlab/cli.py` (add after `run_setup`, near the other top-level `@app.command`s)
- Test: `tests/test_cli_bootstrap.py`

**Interfaces:**
- Consumes (existing, all module-level in `cli.py`): `_lookup_setup_or_exit(name: str) -> Setup`; `_prepare_lab() -> None`; `net_create(pattern: str, step: bool = False) -> None`; `vm_create(pattern: str, manifest: str | None = None, step: bool = False) -> None`; `obs_up(step: bool = False) -> None`. Each raises `typer.Exit` on failure.
- Produces: `_bootstrap_run(setup_name: str, *, manifest: str | None, step: bool) -> None` and `@app.command("bootstrap")` `bootstrap(setup_name, manifest=None, step=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_bootstrap.py`:

```python
from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from mqlab import cli


def _patch_phases(monkeypatch) -> list[tuple[str, tuple, dict]]:
    """Replace the bring-up phases with recorders; return the call log."""
    calls: list[tuple[str, tuple, dict]] = []
    monkeypatch.setattr(cli, "_prepare_lab", lambda: calls.append(("prepare", (), {})))
    monkeypatch.setattr(cli, "net_create", lambda *a, **k: calls.append(("net", a, k)))
    monkeypatch.setattr(cli, "vm_create", lambda *a, **k: calls.append(("vm", a, k)))
    monkeypatch.setattr(cli, "obs_up", lambda *a, **k: calls.append(("obs", a, k)))
    monkeypatch.setattr(cli, "_lookup_setup_or_exit", lambda name: name)
    return calls


def test_bootstrap_runs_phases_in_order(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    cli._bootstrap_run("distributed-pcmk-ubuntu", manifest=None, step=False)

    assert [c[0] for c in calls] == ["prepare", "net", "vm", "obs"]
    # vm phase gets the setup name + manifest threaded through
    vm_call = next(c for c in calls if c[0] == "vm")
    assert vm_call[1][0] == "distributed-pcmk-ubuntu"
    assert vm_call[2]["manifest"] is None


def test_bootstrap_threads_manifest_and_step(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    cli._bootstrap_run("distributed-pcmk-ubuntu", manifest="2026-06-01", step=True)

    net_call = next(c for c in calls if c[0] == "net")
    vm_call = next(c for c in calls if c[0] == "vm")
    obs_call = next(c for c in calls if c[0] == "obs")
    assert net_call[2]["step"] is True
    assert vm_call[2] == {"manifest": "2026-06-01", "step": True}
    assert obs_call[2]["step"] is True


def test_bootstrap_halts_on_phase_failure(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    def boom(*a, **k):
        raise typer.Exit(code=1)

    monkeypatch.setattr(cli, "vm_create", boom)

    with pytest.raises(typer.Exit) as exc:
        cli._bootstrap_run("distributed-pcmk-ubuntu", manifest=None, step=False)

    assert exc.value.exit_code == 1
    # obs phase must NOT run after vm fails
    assert [c[0] for c in calls] == ["prepare", "net"]


def test_bootstrap_unknown_setup_exits_2(monkeypatch) -> None:
    def reject(name):
        raise typer.Exit(code=2)

    monkeypatch.setattr(cli, "_lookup_setup_or_exit", reject)

    with pytest.raises(typer.Exit) as exc:
        cli._bootstrap_run("nope", manifest=None, step=False)

    assert exc.value.exit_code == 2


def test_bootstrap_command_is_wired(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_bootstrap_run",
        lambda setup_name, *, manifest, step: seen.update(
            setup_name=setup_name, manifest=manifest, step=step
        ),
    )

    result = CliRunner().invoke(cli.app, ["bootstrap", "distributed-pcmk-ubuntu"])

    assert result.exit_code == 0
    assert seen == {"setup_name": "distributed-pcmk-ubuntu", "manifest": None, "step": False}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_cli_bootstrap.py -v`
Expected: FAIL with `AttributeError: module 'mqlab.cli' has no attribute '_bootstrap_run'`.

- [ ] **Step 3: Implement the helper + command**

In `src/mqlab/cli.py`, add immediately after the `run_setup` command (end of the `@app.command("run")` block):

```python
def _bootstrap_run(setup_name: str, *, manifest: str | None, step: bool) -> None:
    """Bring a setup all the way up: host gate → networks → guests (create +
    provision) → observability. A thin sequencing wrapper over the existing
    verbs; each phase fails loud (raises typer.Exit) and halts the rest."""
    _lookup_setup_or_exit(setup_name)  # validate the setup name early (exit 2 if unknown)
    _prepare_lab()  # host-arch / KVM / tools gate — fail loud before touching anything
    net_create("all", step=step)
    vm_create(setup_name, manifest=manifest, step=step)
    obs_up(step=step)


@app.command("bootstrap")
def bootstrap(  # pragma: no cover - thin delegator; logic covered via _bootstrap_run
    setup_name: Annotated[str, typer.Argument(help="setup to bring up (e.g. distributed-pcmk-ubuntu)")],
    manifest: _ManifestOpt = None,
    step: _StepFlag = False,
) -> None:
    """Bring up a whole setup in one command: networks → guests → observability.

    This is the consumer happy path. Run `mqlab doctor` first to pre-flight the
    host. (`mqlab run` is the separate post-bring-up baseline test driver.)"""
    _bootstrap_run(setup_name, manifest=manifest, step=step)
```

> The `# pragma: no cover` on `bootstrap` matches the existing `run_setup` convention for verbs that drive the live lab; `test_bootstrap_command_is_wired` still exercises the registration path with `_bootstrap_run` stubbed, and `_bootstrap_run` itself is fully covered by the other tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_cli_bootstrap.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Run full validation (coverage gate)**

Run: `cd .worktrees/issue-299-signed-tarball-release && vrg-container-run -- vrg-validate`
Expected: PASS (ruff, mypy strict, 100% branch coverage).

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_bootstrap.py
vrg-commit --type feat --scope cli --message "add 'mqlab bootstrap <setup>' one-command lab bring-up (#299)"
```

---

## Task 3: Consumer environment-setup script — `scripts/setup`

**Files:**
- Create: `scripts/setup`
- Test: `tests/test_setup_script.py`

**Interfaces:**
- Consumes: `mqlab bootstrap` (Task 2) — referenced in the script's printed "next command".
- Produces: an executable `scripts/setup` that the README and the curation test (Task 1's `INCLUDED` is by directory; `scripts/` already ships) reference.

- [ ] **Step 1: Write the failing test**

Create `tests/test_setup_script.py`:

```python
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "scripts" / "setup"


def test_setup_script_exists_and_is_executable() -> None:
    assert SETUP.is_file(), "scripts/setup is missing"
    assert os.stat(SETUP).st_mode & stat.S_IXUSR, "scripts/setup is not executable"


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_setup_script.py -v`
Expected: FAIL (`scripts/setup is missing`).

- [ ] **Step 3: Create `scripts/setup`**

```bash
#!/usr/bin/env bash
# scripts/setup — prepare the Python environment so `mqlab` runs, then point the
# operator at lab bring-up. This sets up the ENVIRONMENT only; it does NOT bring
# the lab up (that is `mqlab bootstrap <setup>`).
#
# Targets macOS and Linux. Assumes you can install host packages (root/sudo) on a
# virtualization-capable host; `mqlab doctor` checks the host itself.
set -euo pipefail

die() { echo "setup: $*" >&2; exit 1; }

# 1. Prerequisite checks (fail loud, no silent fallback).
command -v uv >/dev/null 2>&1 || die "uv not found — install it: https://docs.astral.sh/uv/getting-started/installation/"

PYTHON_OK=$(uv python find 3.12 >/dev/null 2>&1 && echo yes || echo no)
[ "$PYTHON_OK" = "yes" ] || die "Python 3.12 not available to uv — install it (e.g. 'uv python install 3.12')"

# 2. Materialize the environment.
echo "setup: syncing the Python environment (uv sync)…"
uv sync

# 3. Point the operator at the next steps.
cat <<'NEXT'

setup: environment ready.

Next steps:
  1. Pre-flight the host:      mqlab doctor
  2. Bring up a setup:         mqlab bootstrap <setup>     (e.g. distributed-pcmk-ubuntu)

Run mqlab via the synced environment, e.g.:
  uv run mqlab doctor
  uv run mqlab bootstrap distributed-pcmk-ubuntu

(Or activate the venv: '. .venv/bin/activate' then 'mqlab ...'.)
NEXT
```

- [ ] **Step 4: Make it executable**

Run: `cd .worktrees/issue-299-signed-tarball-release && chmod +x scripts/setup`

- [ ] **Step 5: Run test to verify it passes**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_setup_script.py -v`
Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add scripts/setup tests/test_setup_script.py
vrg-commit --type feat --scope release --message "add scripts/setup consumer environment bootstrap (#299)"
```

---

## Task 4: Release version guard — `tools/release_version_guard.py`

**Files:**
- Create: `tools/release_version_guard.py`
- Test: `tests/test_release_version_guard.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_tag_version(ref: str) -> str`; `read_pyproject_version(path: Path) -> str`; `read_version_file(path: Path) -> str`; `assert_versions_match(tag_version: str, pyproject_version: str, version_file: str) -> None` (raises `SystemExit` on mismatch); `main(argv: list[str]) -> None`. Invoked by the release workflow (Task 5) as `python tools/release_version_guard.py "$GITHUB_REF_NAME"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_release_version_guard.py`:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_GUARD_PATH = REPO_ROOT / "tools" / "release_version_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_version_guard", _GUARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_tag_version_strips_leading_v() -> None:
    g = _load()
    assert g.parse_tag_version("v1.2.0") == "1.2.0"


def test_parse_tag_version_passthrough_without_v() -> None:
    g = _load()
    assert g.parse_tag_version("1.2.0") == "1.2.0"


def test_read_pyproject_version(tmp_path) -> None:
    g = _load()
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "x"\nversion = "3.4.5"\n')
    assert g.read_pyproject_version(p) == "3.4.5"


def test_read_version_file_strips_whitespace(tmp_path) -> None:
    g = _load()
    p = tmp_path / "VERSION"
    p.write_text("3.4.5\n")
    assert g.read_version_file(p) == "3.4.5"


def test_assert_versions_match_passes_when_equal() -> None:
    g = _load()
    g.assert_versions_match("1.2.0", "1.2.0", "1.2.0")  # no raise


def test_assert_versions_match_raises_on_mismatch() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.assert_versions_match("1.2.0", "1.2.1", "1.2.0")


def test_main_ok_against_repo_files() -> None:
    # The repo's pyproject.toml and VERSION agree; tag must match them.
    g = _load()
    version = g.read_version_file(REPO_ROOT / "VERSION")
    g.main(["prog", f"v{version}"])  # no raise


def test_main_usage_error_without_tag() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.main(["prog"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_release_version_guard.py -v`
Expected: FAIL (file does not exist → import error).

- [ ] **Step 3: Implement the guard**

Create `tools/release_version_guard.py`:

```python
#!/usr/bin/env python3
"""Fail loud unless the release tag, pyproject.toml version, and VERSION agree.

Invoked by .github/workflows/release.yml before building the tarball:
    python tools/release_version_guard.py "$GITHUB_REF_NAME"
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def parse_tag_version(ref: str) -> str:
    """'v1.2.0' -> '1.2.0'; a bare '1.2.0' passes through unchanged."""
    return ref[1:] if ref.startswith("v") else ref


def read_pyproject_version(path: Path) -> str:
    return tomllib.loads(path.read_text())["project"]["version"]


def read_version_file(path: Path) -> str:
    return path.read_text().strip()


def assert_versions_match(tag_version: str, pyproject_version: str, version_file: str) -> None:
    if not tag_version == pyproject_version == version_file:
        raise SystemExit(
            "release version mismatch: "
            f"tag={tag_version!r} pyproject={pyproject_version!r} VERSION={version_file!r}"
        )


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: release_version_guard.py <tag>")
    root = Path(__file__).resolve().parent.parent
    tag_version = parse_tag_version(argv[1])
    assert_versions_match(
        tag_version,
        read_pyproject_version(root / "pyproject.toml"),
        read_version_file(root / "VERSION"),
    )
    print(f"ok: release version {tag_version}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd .worktrees/issue-299-signed-tarball-release && uv run pytest tests/test_release_version_guard.py -v`
Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add tools/release_version_guard.py tests/test_release_version_guard.py
vrg-commit --type feat --scope release --message "add release version guard (tag == pyproject == VERSION) (#299)"
```

---

## Task 5: Release workflow + signing key + operator runbook

This task has a **non-TDD operator step** (PGP key generation cannot be unit-tested). The testable logic (the version guard) is already covered by Task 4. Correctness of the end-to-end pipeline is proven by the dry run in Step 5 and by the acceptance run (spec §10).

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `RELEASE-KEY.asc` (public key — convenience copy)
- Create: `docs/development/release-runbook.md`

**Interfaces:**
- Consumes: `tools/release_version_guard.py` (Task 4); `.gitattributes` (Task 1).
- Produces: a GitHub Release per `vX.Y.Z` tag with four assets (`.tar.gz`, `.tar.gz.asc`, `SHA256SUMS`, `SHA256SUMS.asc`).

- [ ] **Step 1: Operator — generate the dedicated release signing subkey (manual)**

Run locally (operator action; the human runs these via `! <command>` if needed):

```bash
# Generate a dedicated release key (no expiry shown for brevity; set one in practice).
gpg --batch --quick-generate-key "mq-cluster-tooling release <release@logical-minds-foundry>" ed25519 sign
# Capture the fingerprint (used below and in the README):
gpg --fingerprint release@logical-minds-foundry
# Export the PUBLIC key for the repo:
gpg --armor --export release@logical-minds-foundry > RELEASE-KEY.asc
# Export the PRIVATE key for the CI secret (store securely; do NOT commit):
gpg --armor --export-secret-keys release@logical-minds-foundry > /tmp/release-private.asc
```

Record the full fingerprint — Task 6 (README) needs it.

- [ ] **Step 2: Operator — store CI secrets**

In the GitHub repo settings → Secrets and variables → Actions, add:
- `RELEASE_GPG_PRIVATE_KEY` — contents of `/tmp/release-private.asc`.
- `RELEASE_GPG_PASSPHRASE` — the key passphrase (OPTIONAL: only needed if the signing key has a
  passphrase; leave it unset if the key has no passphrase — the workflow tolerates an empty value).

Then securely delete `/tmp/release-private.asc`.

- [ ] **Step 3: Commit the public key**

```bash
vrg-git add RELEASE-KEY.asc
vrg-commit --type feat --scope release --message "add public release signing key (convenience copy) (#299)"
```

- [ ] **Step 4: Create the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout at the tag
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Guard — tag matches pyproject.toml and VERSION
        run: python tools/release_version_guard.py "${GITHUB_REF_NAME}"

      - name: Build curated tarball
        run: |
          set -euo pipefail
          PKG="mq-cluster-tooling-${GITHUB_REF_NAME}"
          git archive --prefix="${PKG}/" -o "${PKG}.tar.gz" "${GITHUB_REF_NAME}"
          sha256sum "${PKG}.tar.gz" > SHA256SUMS
          echo "PKG=${PKG}" >> "${GITHUB_ENV}"

      - name: Import signing key
        run: |
          set -euo pipefail
          echo "${{ secrets.RELEASE_GPG_PRIVATE_KEY }}" | gpg --batch --import

      - name: Sign tarball and checksums
        env:
          PASSPHRASE: ${{ secrets.RELEASE_GPG_PASSPHRASE }}
        run: |
          set -euo pipefail
          gpg --batch --yes --pinentry-mode loopback --passphrase "${PASSPHRASE}" \
            --detach-sign --armor "${PKG}.tar.gz"
          gpg --batch --yes --pinentry-mode loopback --passphrase "${PASSPHRASE}" \
            --detach-sign --armor SHA256SUMS

      - name: Publish GitHub Release (idempotent — §6 convergence)
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          # Create the release only if absent, then upload with --clobber so a
          # re-run of the tag's workflow converges to the same four assets
          # instead of erroring on an already-existing release (design §6).
          gh release view "${GITHUB_REF_NAME}" >/dev/null 2>&1 \
            || gh release create "${GITHUB_REF_NAME}" \
                 --title "${GITHUB_REF_NAME}" \
                 --notes "Signed release ${GITHUB_REF_NAME}. Verify with RELEASE-KEY.asc (see README — fetch the key out-of-band)."
          gh release upload --clobber "${GITHUB_REF_NAME}" \
            "${PKG}.tar.gz" "${PKG}.tar.gz.asc" SHA256SUMS SHA256SUMS.asc
```

> **`[publish] release = false` stays as-is.** This is a bespoke release job (the design's default, §9). The existing `cd.yml` (docs) is untouched. If `vergil-actions` later exposes a custom-artifact release path, migration is a follow-up — not blocking.

- [ ] **Step 5: Dry-run the build locally (no publish)**

Run (verifies archive + checksum + the guard against a real tag string):

```bash
cd .worktrees/issue-299-signed-tarball-release
python tools/release_version_guard.py "v$(cat VERSION)"
# File-free: pipe the archive listing straight to grep (no hardcoded build/ path).
git archive --worktree-attributes --prefix="mq-cluster-tooling-test/" HEAD \
  | tar tzf - \
  | grep -E 'mq-cluster-tooling-test/(scripts/setup|RELEASE-KEY.asc|src/mqlab/cli.py)$'
# And confirm dev-only paths are absent:
git archive --worktree-attributes --prefix="mq-cluster-tooling-test/" HEAD \
  | tar tzf - \
  | grep -E 'mq-cluster-tooling-test/(vergil.toml|tests/|.github/)' && echo "LEAK" || echo "clean"
```

Expected: guard prints `ok: release version <X.Y.Z>`; the first `grep` lists `scripts/setup`, `RELEASE-KEY.asc`, and `src/mqlab/cli.py` (the curated tree carries the consumer entrypoints); the second prints `clean` (no dev-only paths leaked). No files written.

- [ ] **Step 6: Create the operator runbook**

Create `docs/development/release-runbook.md` capturing Steps 1–2 (keygen, secrets), the tag-and-push procedure, and the consumer verification steps. Content:

```markdown
# Release runbook

Releases are published by `.github/workflows/release.yml` on a `vX.Y.Z` tag push.

## One-time setup

1. Generate the dedicated release signing subkey (see commands in the
   signed-tarball plan, Task 5 Step 1) and record its full fingerprint.
2. Store `RELEASE_GPG_PRIVATE_KEY` and `RELEASE_GPG_PASSPHRASE` as GitHub Actions
   secrets. Commit only the public `RELEASE-KEY.asc`.
3. Publish the fingerprint in the README and upload the public key to a keyserver.

## Cutting a release

1. Bump `version` in `pyproject.toml` and the `VERSION` file (same value), commit.
2. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`
   (the human runs raw git via `! …`; the version guard fails the job if the tag,
   pyproject, and VERSION disagree).
3. The workflow archives, checksums, signs, and publishes the Release.

## Consumer verification (document in README)

The trust root is the fingerprint + an out-of-band key — never the copy inside
the tarball. See README "Getting Started → verify".
```

- [ ] **Step 7: Commit**

```bash
vrg-git add .github/workflows/release.yml docs/development/release-runbook.md
vrg-commit --type feat --scope release --message "add tag-triggered signed-release workflow + runbook (#299)"
```

---

## Task 6: README rewrite (users-first)

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/setup` (Task 3), `mqlab bootstrap` (Task 2), `RELEASE-KEY.asc` + fingerprint (Task 5), prerequisites (design §7.1).
- Produces: the consumer's first-read document.

- [ ] **Step 1: Rewrite `README.md`**

Replace the entire file with the structure below. **Replace `<RELEASE-KEY-FINGERPRINT>`** with the fingerprint recorded in Task 5 Step 1 (`gpg --fingerprint release@logical-minds-foundry`).

```markdown
# mq-cluster-tooling

Tooling, lab, and operating standards for running IBM MQ high-availability
queue managers on Linux clusters — packaged as a downloadable, signed release
you can stand up end-to-end with one command.

## What this is

A reproducible lab plus the `mqlab` orchestrator that brings up, configures, and
fails over IBM MQ queue managers across HA/DR topologies. The deliverable is the
**whole tree** (orchestrator + Ansible + lab definitions + manifests), not a
Python module — you run it from the extracted release.

## Getting Started

### Prerequisites

Almost everything is fetched automatically — IBM **MQ Advanced for Developers**
(no-charge) is pulled by the tooling; the virtualization stack is checked by
`mqlab doctor`. You provide:

- **A virtualization-capable Linux/macOS host with root/sudo** — the lab creates
  nested libvirt/QEMU/Vagrant guests and wants a beefy box (~12 vCPU / 64 GiB,
  nested virtualization). `mqlab doctor` reports anything missing.
- **A RHEL box/subscription** — only for the RHEL-based arms (pcmk-rhel, RDQM).
  Ubuntu arms need nothing extra.

### (Optional) Verify the download

The trust root is the **fingerprint below plus a key fetched out-of-band** — not
the `RELEASE-KEY.asc` bundled in the tarball.

```
gpg --recv-keys <RELEASE-KEY-FINGERPRINT>     # or import the key from the release page over HTTPS
gpg --fingerprint <RELEASE-KEY-FINGERPRINT>   # cross-check it matches this README
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

### Run it

```
./scripts/setup                         # checks prereqs, runs uv sync
uv run mqlab doctor                     # pre-flight the host
uv run mqlab bootstrap <setup>          # bring the lab up, then sit back
```

#### What `scripts/setup` does

It sets up the **environment**, not the lab:

1. Checks `uv` and Python 3.12 are available (fails loud if not).
2. Runs `uv sync` to materialize the virtual environment.
3. Prints the next commands (`mqlab doctor`, `mqlab bootstrap <setup>`).

You can do these by hand instead. `mqlab bootstrap <setup>` then sequences the
lab bring-up: networks → guests (create + provision) → observability.

## Development

This repo is developed inside an ephemeral, reproducible Vergil VM, not on the
host directly. Footprint and tooling are declared as the `[vm.vergil-user]`
profile in `vergil.toml`; build and enter the box with
`vrg-vm create logical-minds-foundry/mq-cluster-tooling --identity vergil-user`
then
`vrg-vm session logical-minds-foundry/mq-cluster-tooling --identity vergil-user`.
See `CLAUDE.md` for the workflow and `docs/development/release-runbook.md` for
cutting a release.
```

- [ ] **Step 2: Validate (markdownlint runs inside vrg-validate)**

Run: `cd .worktrees/issue-299-signed-tarball-release && vrg-container-run -- vrg-validate`
Expected: PASS (markdownlint clean; tests still green).

- [ ] **Step 3: Commit**

```bash
vrg-git add README.md
vrg-commit --type docs --scope readme --message "rewrite README users-first (intro → getting started → development) (#299)"
```

---

## Final verification

- [ ] **Full validation:** `cd .worktrees/issue-299-signed-tarball-release && vrg-container-run -- vrg-validate` — ruff, mypy strict, 100% branch coverage, markdownlint all green.
- [ ] **Curation dry-run:** Task 5 Step 5 lists `scripts/setup`, `RELEASE-KEY.asc`, `src/mqlab/cli.py` in the archived tree and prints `clean` for dev-only paths.
- [ ] **Open the PR** into `develop` with `vrg-submit-pr` (or `vrg-gh pr create --base develop`), linking #299.

### Manual acceptance (post-merge — real environment)

These spec §10 criteria can only be exercised outside the validate container; they
are deferred but must not be forgotten:

- [ ] **Setup smoke (host):** on a clean **Linux** host and a clean **macOS** host with `uv` present, run `./scripts/setup` and confirm it reaches a working `mqlab --help` (then `mqlab doctor`).
- [ ] **End-to-end release (post-merge):** after this lands on `main` and a `vX.Y.Z` tag is pushed, confirm the GitHub Release publishes all four assets, and that a fresh consumer can fetch the key out-of-band, `gpg --verify`, unpack, `./scripts/setup`, pass `mqlab doctor`, and `mqlab bootstrap <setup>`.

## Spec coverage check

- §3 (three committed pieces + CI job): `.gitattributes` (T1), `scripts/setup` (T3), `README.md` (T6), `release.yml` (T5). ✓
- §4 (curation): T1 + test. ✓
- §5 (signing, out-of-band trust root): T5 key + workflow signing; T6 README verify steps. ✓
- §6 (trigger + fail-loud version guard): T4 guard + T5 workflow `on: push: tags`. ✓
- §7 / §7.1 (`scripts/setup`, prerequisites): T3 + T6. ✓
- §7.2 (`mqlab bootstrap` wrapper): T2 + tests. ✓
- §8 (README structure): T6. ✓
- §9 (publish-flag decision): T5 Step 4 note — bespoke job, `release = false` kept. ✓
- §10 (tests/acceptance): per-task tests + final verification. ✓
```
