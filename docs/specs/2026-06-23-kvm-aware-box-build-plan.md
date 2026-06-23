# KVM-aware RHEL Box Build — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the one-time RHEL 9.6 box build run under native KVM on an x86 host (and stay TCG on the arm64 Mac), instead of being hardcoded to TCG everywhere.

**Architecture:** `platforms.build_domain_virt(facts)` is the single authority that maps host facts → `(domain_type, cpu_mode)`. The orchestrator (`cli._box_build_steps`) computes it and passes the result to `build-box.sh` as **required CLI args** (`--domain-type`/`--cpu-mode`). `build-box.sh` is a thin consumer: it validates the args (usage-die if missing/invalid), substitutes them into `build-domain.xml.tpl`, and branches the install message. No call back into mqlab; one interface for human and harness.

**Tech Stack:** Python 3.12 + Typer (CLI), pytest (TDD), libvirt/`virsh` + `qemu` (the build domain), bash (`build-box.sh`).

**Design spec:** `docs/specs/2026-06-23-kvm-aware-box-build-design.md` (Issue #327).

## Global Constraints

- **Python:** `requires-python >=3.12`; target `py312`.
- **Validation is one command:** `vrg-container-run -- vrg-validate` (ruff + mypy `strict` + pytest with **100% branch coverage**). It is the authoritative per-task gate before every commit. Per-step red/green may run `uv run pytest <path> -v` directly in the worktree (the repo's plans do this), but the commit gate is always `vrg-validate`.
- **Git/GitHub local wrappers:** use `vrg-git` and `vrg-commit` (conventional commits). Raw `git`/`gh` are denied locally.
- **Worktree:** all work happens in `.worktrees/issue-327-kvm-aware-box-build/` on branch `feature/327-kvm-aware-box-build`. Run every command from there.
- **Single authority (D1):** the KVM/TCG rule lives only in `platforms.build_domain_virt`. `build-box.sh` must NOT re-derive it in bash.
- **Fail loud (D5):** no swallowed exceptions, no silent fallbacks. Missing/invalid build-box.sh args → usage message + non-zero exit.
- **No fabricated KVM duration (D4):** the KVM message asserts "native virtualization, much faster than the TCG path" — no invented minutes figure.
- **Matrix constants** (already in `src/mqlab/platforms.py`): `X86_64` (from `hostfacts`), `CPU_KVM = "host-passthrough"`, `CPU_TCG = "maximum"`. Reuse them; do not redefine.
- **Accepted arg values:** `--domain-type` ∈ {`kvm`, `qemu`}; `--cpu-mode` ∈ {`host-passthrough`, `maximum`}.

---

## File Structure

- **Modify** `src/mqlab/platforms.py` — add the pure `build_domain_virt(facts)` next to `_provider`.
- **Modify** `tests/test_platforms.py` — matrix tests for `build_domain_virt`.
- **Modify** `src/mqlab/cli.py` — `_box_build_steps` gains a `facts` param and appends the two args; `_ensure_local_boxes` probes facts and passes them down; fix the stale `_LOCAL_BOX_BUILDERS` comment.
- **Modify** `tests/test_cli_vm.py` — assert the box-build `Command` argv carries the decision args (KVM and TCG cases), with injected facts.
- **Modify** `lab/boxes/rhel96/build-domain.xml.tpl` — parameterize `type` and `cpu mode`.
- **Modify** `lab/boxes/rhel96/build-box.sh` — parse + validate the two args, substitute them, branch the message, fix duration comments.
- **Create** `tests/test_build_box_usage.py` — subprocess test of the usage-die path (no virsh needed).

---

## Task 1: `platforms.build_domain_virt` — the decision authority

**Files:**
- Modify: `src/mqlab/platforms.py` (add function after `_provider`, ~line 113)
- Test: `tests/test_platforms.py`

**Interfaces:**
- Consumes: `HostFacts` (`arch`, `kvm`), and the existing module constants `X86_64`, `CPU_KVM`, `CPU_TCG`.
- Produces: `build_domain_virt(facts: HostFacts) -> tuple[str, str]` returning `(domain_type, cpu_mode)` — `("kvm", "host-passthrough")` when `facts.arch == X86_64 and facts.kvm`, else `("qemu", "maximum")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_platforms.py` (the fixtures `X86_KVM`, `X86_NOKVM`, `ARM_KVM` already exist at the top of that file):

```python
def test_build_domain_virt_x86_host_with_kvm_is_native():
    assert p.build_domain_virt(X86_KVM) == ("kvm", "host-passthrough")


def test_build_domain_virt_x86_host_without_kvm_falls_back_to_tcg():
    # Defensive: the gated bring-up never reaches here (require_native_kvm), but
    # the pure function stays honest and display-safe.
    assert p.build_domain_virt(X86_NOKVM) == ("qemu", "maximum")


def test_build_domain_virt_arm_host_is_foreign_tcg():
    # x86_64 guest on an arm64 Mac is foreign-arch — must be TCG.
    assert p.build_domain_virt(ARM_KVM) == ("qemu", "maximum")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_platforms.py -k build_domain_virt -v`
Expected: FAIL with `AttributeError: module 'mqlab.platforms' has no attribute 'build_domain_virt'`.

- [ ] **Step 3: Implement the function**

In `src/mqlab/platforms.py`, immediately after `_provider` (before `render_resolved`, ~line 113), add:

```python
def build_domain_virt(facts: HostFacts) -> tuple[str, str]:
    """(domain_type, cpu_mode) for the local x86_64 RHEL box build.

    KVM when the host natively virtualizes x86_64; TCG otherwise (foreign-arch
    arm64 Mac, or an x86 host without usable /dev/kvm). Pure and display-safe —
    never raises — mirroring resolve(). The single authority for the box-build
    domain's virtualization (design D1); build-box.sh consumes the result, it
    does not re-derive it.
    """
    kvm = facts.arch == X86_64 and facts.kvm
    return ("kvm", CPU_KVM) if kvm else ("qemu", CPU_TCG)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_platforms.py -k build_domain_virt -v`
Expected: 3 PASS.

- [ ] **Step 5: Full validation gate**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && vrg-container-run -- vrg-validate`
Expected: PASS (ruff, mypy strict, 100% branch coverage). The new function's single branch is covered by the KVM and TCG test cases.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope platforms --message "add build_domain_virt — host-arch KVM/TCG decision for the box build (#327)"
```

---

## Task 2: Orchestrator passes the decision as args

**Files:**
- Modify: `src/mqlab/cli.py` — `_box_build_steps` (~line 734), `_ensure_local_boxes` (~line 792), the `_LOCAL_BOX_BUILDERS` comment (~line 692), and the `platforms` import (~line 52)
- Test: `tests/test_cli_vm.py` (update `test_ensure_local_boxes_builds_missing` ~line 512; add a direct `_box_build_steps` test)

**Interfaces:**
- Consumes: `platforms.build_domain_virt` (Task 1); `hostfacts.probe` (already imported in `cli.py:25`); `HostFacts`.
- Produces: `_box_build_steps(needed: dict[str, str], present: dict[str, str], facts: HostFacts) -> list[CommandStep]` — each built box's `Command.argv` is `["bash", <build-box.sh>, "--domain-type", <dtype>, "--cpu-mode", <cpu>]`. `_ensure_local_boxes(guests)` calls `probe()` and threads the facts in.

- [ ] **Step 1: Write the failing tests**

First, add the `platforms` symbol the tests will reference. At the top of `tests/test_cli_vm.py`, ensure these imports exist (add what's missing):

```python
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
```

Add a direct unit test for the pure step-builder (put it near the other `_box_build_steps`/`_ensure_local_boxes` tests):

```python
def test_box_build_steps_passes_kvm_args(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert [s.command.argv for s in steps] == [
        ["bash", str(tmp_path / "lab/boxes/rhel96/build-box.sh"),
         "--domain-type", "kvm", "--cpu-mode", "host-passthrough"],
    ]


def test_box_build_steps_passes_tcg_args_on_arm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert steps[0].command.argv[-4:] == ["--domain-type", "qemu", "--cpu-mode", "maximum"]
```

Then update the existing `test_ensure_local_boxes_builds_missing` (~line 512) to inject facts and assert the new argv. Replace its body with:

```python
def test_ensure_local_boxes_builds_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64}\n")
    monkeypatch.setattr(
        cli, "probe", lambda: HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    )
    runner = RecordingRunner(
        results=[ScriptedResult(["There are no installed boxes!"]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["rdqm-a1"])
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == ["vagrant", "box", "list"]
    assert argvs[1] == [
        "bash", str(tmp_path / "lab/boxes/rhel96/build-box.sh"),
        "--domain-type", "kvm", "--cpu-mode", "host-passthrough",
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_cli_vm.py -k "box_build_steps or ensure_local_boxes_builds_missing" -v`
Expected: FAIL — `test_box_build_steps_*` fail with `TypeError: _box_build_steps() takes 2 positional arguments but 3 were given`; the builds_missing test fails on the argv assertion (no args yet).

- [ ] **Step 3: Add the `build_domain_virt` import**

In `src/mqlab/cli.py`, extend the existing platforms import (line ~52):

```python
from mqlab.platforms import PlatformError, build_domain_virt, ensure_resolved
```

- [ ] **Step 4: Thread facts through `_box_build_steps`**

Replace `_box_build_steps` (~line 734) with:

```python
def _box_build_steps(
    needed: dict[str, str], present: dict[str, str], facts: HostFacts
) -> list[CommandStep]:
    # The local-built box is RHEL x86_64; build_domain_virt is the single authority
    # for whether that build runs under KVM (native x86 host) or TCG (#327).
    domain_type, cpu_mode = build_domain_virt(facts)
    return [
        CommandStep(
            f"box {box}",
            Command(  # noqa: S607
                ["bash", str(repo_root() / script),
                 "--domain-type", domain_type, "--cpu-mode", cpu_mode]
            ),
        )
        for box, script in sorted(needed.items())
        if box not in present
    ]
```

Add the `HostFacts` import if `cli.py` does not already import it. Check the existing `from mqlab.hostfacts import ...` line (~line 25, currently `import probe`) and extend it:

```python
from mqlab.hostfacts import HostFacts, probe
```

- [ ] **Step 5: Pass facts from `_ensure_local_boxes`**

In `_ensure_local_boxes` (~line 792), update the box-list branch to probe and pass facts. Change:

```python
        if needed:
            cmd = Command(["vagrant", "box", "list"], cwd=repo_root() / "lab")  # noqa: S607
            steps += _box_build_steps(needed, _probe(deps, cmd, parse_box_list))
```

to:

```python
        if needed:
            cmd = Command(["vagrant", "box", "list"], cwd=repo_root() / "lab")  # noqa: S607
            steps += _box_build_steps(needed, _probe(deps, cmd, parse_box_list), probe())
```

- [ ] **Step 6: Fix the stale comment**

Replace the `_LOCAL_BOX_BUILDERS` comment (~line 692) with:

```python
# Boxes built locally (not on Vagrant Cloud) -> their build script. build-box.sh
# REUSEs the host-durable build/state/boxes cache when present (a quick `vagrant box add`)
# and only does the ISO build on a truly first-ever run — ~45-90 min under TCG
# (arm64 Mac), minutes under KVM on a native-x86 host (#276/#291/#327).
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_cli_vm.py -k "box_build_steps or ensure_local_boxes" -v`
Expected: the two new `box_build_steps` tests PASS; `test_ensure_local_boxes_builds_missing` PASSES; the two noop tests (`..._noop_when_box_present`, `..._noop_when_no_local_box`) still PASS (they build no step, so the new argv is absent — they call `probe()` on the real host but the result is unused). If either noop test is non-deterministic in CI, add `monkeypatch.setattr(cli, "probe", lambda: HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True))` to it; the argv assertions do not change.

- [ ] **Step 8: Full validation gate**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && vrg-container-run -- vrg-validate`
Expected: PASS (ruff, mypy strict, 100% branch coverage).

- [ ] **Step 9: Commit**

```bash
vrg-commit --type feat --scope cli --message "pass host-resolved domain-type/cpu-mode args to build-box.sh (#327)"
```

---

## Task 3: `build-box.sh` consumes the args; template parameterized; usage-die test

**Files:**
- Modify: `lab/boxes/rhel96/build-domain.xml.tpl` (lines 1, 5, 15)
- Modify: `lab/boxes/rhel96/build-box.sh` (header comment line 5; arg loop lines 18–26; comment line 68; sed line 103; message line 112)
- Create: `tests/test_build_box_usage.py`

**Interfaces:**
- Consumes: the `--domain-type`/`--cpu-mode` args produced by Task 2.
- Produces: a `build-domain.xml.tpl` with `@DOMAIN_TYPE@`/`@CPU_MODE@`/`@ISO@` placeholders; a `build-box.sh` that exits non-zero with a usage message when the args are missing/invalid.

- [ ] **Step 1: Write the failing usage-die test**

Create `tests/test_build_box_usage.py`:

```python
"""build-box.sh must require --domain-type/--cpu-mode and die loudly when they are
missing — the orchestrator always supplies them; a hand-run is told what to pass
(#327, design D3/D5). This exercises only the early arg-validation path, which
runs before any git/virsh/cache side effect, so it needs no libvirt."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "rhel96" / "build-box.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_args_dies_with_usage():
    result = _run()
    assert result.returncode != 0
    assert "--domain-type" in (result.stderr + result.stdout)


def test_missing_cpu_mode_dies_with_usage():
    result = _run("--domain-type", "kvm")
    assert result.returncode != 0
    assert "--cpu-mode" in (result.stderr + result.stdout)


def test_invalid_domain_type_dies():
    result = _run("--domain-type", "bogus", "--cpu-mode", "maximum")
    assert result.returncode != 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_build_box_usage.py -v`
Expected: FAIL — with no validation yet, `build-box.sh` either runs past arg parsing (and fails later for unrelated reasons / wrong message) or treats the flags as unknown args. The assertions on `--domain-type`/`--cpu-mode` in output do not hold.

- [ ] **Step 3: Parameterize the template**

In `lab/boxes/rhel96/build-domain.xml.tpl`:

Change line 1 (the comment) from:

```xml
<!-- Transient RHEL 9.6 build domain - the #24-proven TCG x86 recipe.
```

to:

```xml
<!-- Transient RHEL 9.6 build domain - host-resolved recipe (#327): @DOMAIN_TYPE@/@CPU_MODE@
     are substituted by build-box.sh from the args mqlab passes (KVM on a native-x86
     host; TCG — the #24-proven x86 recipe — on the arm64 Mac).
```

Change line 5 from `<domain type='qemu'>` to:

```xml
<domain type='@DOMAIN_TYPE@'>
```

Change line 15 from `<cpu mode='maximum'/>` to:

```xml
  <cpu mode='@CPU_MODE@'/>
```

Leave the `<emulator>/usr/bin/qemu-system-x86_64</emulator>` line and everything else unchanged — only `type` and `cpu mode` are host-dependent (design §3).

- [ ] **Step 4: Add arg parsing + validation to `build-box.sh`**

In `lab/boxes/rhel96/build-box.sh`, replace the arg-parsing block (lines 18–26):

```bash
FORCE="${LAB_REBUILD_BOX:-0}"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --rebuild-box) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "ERROR: unknown arg: $arg" >&2; exit 2 ;;
  esac
done
```

with (a `while`/`shift` loop is needed because the new flags take a value):

```bash
FORCE="${LAB_REBUILD_BOX:-0}"
DRY_RUN=0
DOMAIN_TYPE=""
CPU_MODE=""

usage() {
  cat >&2 <<'USAGE'
usage: build-box.sh --domain-type <kvm|qemu> --cpu-mode <host-passthrough|maximum> [--rebuild-box] [--dry-run]

  --domain-type / --cpu-mode are REQUIRED. mqlab normally supplies them
  (it computes them from host facts via platforms.build_domain_virt, #327).
  If you are running this by hand on a native-x86 host, pass:
      --domain-type kvm  --cpu-mode host-passthrough
  on the arm64 Mac (x86 guest is emulated), pass:
      --domain-type qemu --cpu-mode maximum
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --rebuild-box) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --domain-type) DOMAIN_TYPE="${2:-}"; shift ;;
    --cpu-mode) CPU_MODE="${2:-}"; shift ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

case "$DOMAIN_TYPE" in
  kvm|qemu) ;;
  *) echo "ERROR: --domain-type must be 'kvm' or 'qemu' (got '${DOMAIN_TYPE}')" >&2; usage; exit 2 ;;
esac
case "$CPU_MODE" in
  host-passthrough|maximum) ;;
  *) echo "ERROR: --cpu-mode must be 'host-passthrough' or 'maximum' (got '${CPU_MODE}')" >&2; usage; exit 2 ;;
esac
```

This block sits before the `git rev-parse` build-dir resolution (line ~28), so a missing-arg invocation dies before any side effect.

- [ ] **Step 5: Substitute the placeholders**

In `build-box.sh`, change the `sed` (line 103) from:

```bash
sed -e "s|@ISO@|/var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso|" \
  build-domain.xml.tpl > "$WORK/domain.xml"
```

to:

```bash
sed -e "s|@ISO@|/var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso|" \
  -e "s|@DOMAIN_TYPE@|${DOMAIN_TYPE}|" \
  -e "s|@CPU_MODE@|${CPU_MODE}|" \
  build-domain.xml.tpl > "$WORK/domain.xml"
```

- [ ] **Step 6: Branch the install message**

In `build-box.sh`, replace the unconditional message (line 112):

```bash
echo "installing (TCG, expect 45-90 min); waiting for shut off..."
```

with:

```bash
if [ "$DOMAIN_TYPE" = kvm ]; then
  echo "installing (KVM — native virtualization, much faster than the TCG path); waiting for shut off..."
else
  echo "installing (TCG, expect 45-90 min); waiting for shut off..."
fi
```

- [ ] **Step 7: Fix the duration comments**

In `build-box.sh`, change the header line 5 from:

```bash
# .box; ~45-90 min under TCG) and caches it on the HOST-DURABLE, repo-root
```

to:

```bash
# .box; ~45-90 min under TCG on the arm64 Mac, minutes under KVM on a native-x86
# host — #327) and caches it on the HOST-DURABLE, repo-root
```

and change line 68 from:

```bash
# --- Expensive path (BUILD / FORCE-BUILD): the ~45-90 min TCG install. ---
```

to:

```bash
# --- Expensive path (BUILD / FORCE-BUILD): the install (~45-90 min under TCG; minutes under KVM). ---
```

- [ ] **Step 8: Run the usage-die test to verify it passes**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && uv run pytest tests/test_build_box_usage.py -v`
Expected: 3 PASS.

- [ ] **Step 9: Full validation gate**

Run: `cd .worktrees/issue-327-kvm-aware-box-build && vrg-container-run -- vrg-validate`
Expected: PASS. (If `vrg-validate` runs `shellcheck`, the rewritten `build-box.sh` must be clean — the `while`/`case` block and `cat <<'USAGE'` heredoc are shellcheck-safe; `set -euo pipefail` at the top is preserved.)

- [ ] **Step 10: Commit**

```bash
vrg-commit --type feat --scope box-build --message "build-box.sh: KVM-aware build domain via required --domain-type/--cpu-mode args (#327)"
```

---

## Final acceptance

- [ ] **Tier 1 (blocking, automated + Mac):**
  - `cd .worktrees/issue-327-kvm-aware-box-build && vrg-container-run -- vrg-validate` — all green (ruff, mypy strict, 100% branch coverage, the three new test groups).
  - No-harm check on the arm64 Mac: `_box_build_steps` resolves `--domain-type qemu --cpu-mode maximum` (covered by `test_box_build_steps_passes_tcg_args_on_arm`), and a real arm64 box build still produces a working `.box` unchanged. (Run only if a Mac build is convenient; otherwise the unit assertion stands in.)

- [ ] **Tier 2 (blocks "done", manual on the x86 cloud host):**
  - On the native-x86 cloud host, run a fresh box build (`mqlab vm create …` with the RHEL box absent, or `bash lab/boxes/rhel96/build-box.sh --domain-type kvm --cpu-mode host-passthrough --rebuild-box`). Confirm: the message reads `installing (KVM — …)`, `virsh dumpxml rhel96-build` (while running) shows `<domain type='kvm'>`, the build completes far faster than 45–90 min, and `vagrant box add` registers a working box.
  - Confirm a hand-run with no args prints the usage message and exits non-zero.
  - Optionally record the observed KVM build duration (design D4 follow-up).

---

## Self-Review

**Spec coverage:**
- §1/§3 (TCG-pinned domain → host-resolved) → Tasks 1+3. ✓
- §2 D1 (single authority, no bash re-derivation) → Task 1 (function) + Task 3 (script only consumes args). ✓
- §2 D2 (`.tpl` renders placeholders) → Task 3 Step 3. ✓
- §2 D3 (required args, usage-die, one interface) → Task 3 Steps 4 + 1/8 (tests). ✓
- §2 D4 (no fabricated KVM duration) → Task 3 Step 6 message. ✓
- §2 D5 (fail loud) → Task 3 Step 4 validation; `set -euo pipefail` preserved. ✓
- §4.1 `build_domain_virt` → Task 1. ✓
- §4.2 orchestrator integration + facts injection → Task 2. ✓
- §4.3 build-box.sh changes → Task 3 Steps 3–7. ✓
- §1.1 stale `cli.py` comment → Task 2 Step 6. ✓
- §6 testing (platforms matrix, RecordingRunner arg-passing, usage-die) → Tasks 1/2/3 tests. ✓
- §3 emulator/machine unchanged → Task 3 Step 3 note. ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; the `@…@` are intentional template tokens.

**Type consistency:** `build_domain_virt(facts) -> tuple[str, str]` used identically in Task 1 (def) and Task 2 (call); arg flag names `--domain-type`/`--cpu-mode` and value sets match across the spec, cli.py, build-box.sh, and all tests.
