# Plan A — Arm-Backend Seam + pcmk Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data-driven arm-backend seam — an `arms:` registry in `topology.yaml` + a `src/mqlab/arms.py` resolver — refactor the Pacemaker `mqlab qm` commands to dispatch through it, retire P1's `provisional_arm`/`parity.MATRIX` stopgaps, extract the shared distributed playbook, add the `mqweb` role to the in-house pcmk QM, and rename `distributed`→`distributed-pcmk-ubuntu`. Foundation for the RDQM backend (Plans B/C).

**Architecture:** The registry is the single source of arm truth (catalog). A pure-Python resolver answers "for setup X, verb V, what runs?" The `qm` commands stop hardcoding `pcs`/playbooks and dispatch via the resolver. Mechanics stay in Ansible/scripts. No behavior change to the pcmk arm — proven by `mqlab run distributed-pcmk-ubuntu` green (regression net) + a pcmk cold boot.

**Tech Stack:** Python 3.12 (frozen dataclasses, `from __future__ import annotations`), PyYAML, Typer (existing `mqlab` CLI), pytest (`uv run pytest`, 100% branch coverage), Ansible.

---

## Context the implementer needs

- **Work in the worktree** `/Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-199-rdqm-parity-build/` (or a fresh one for the Plan-A issue). Commits via `vrg-commit`. Validation: `vrg-container-run -- vrg-validate` (ruff/mypy/ty/100%-cov/audit). Single test: `uv run pytest tests/test_x.py -v`.
- **Existing seams to follow:** `src/mqlab/setups.py` (`Setup`/`QmConfig`/`lab_setups`/`_topology`), `src/mqlab/paths.py`, `src/mqlab/parity.py` (P1 — `provisional_arm`, `MATRIX`), `src/mqlab/cli.py` (`_qm_playbook`/`_qm_pcs` at 818–897, the `qm` command group at 900–927, `mqlab run` `run_setup`).
- **Registry verb-impl kinds:** `{playbook: <file>}`, `{pcs: <args>}` (later `{cmd:}`/`{script:}` for RDQM in Plan B). The resolver returns the kind+value; the CLI builds the `CommandStep`.
- **Out of scope for Plan A:** RDQM verbs/backends (Plan B), the `dr` command group (Plan C), the 3+3 (Plan C). The registry declares `rdqm-rhel` but with **no verbs** until Plan B's spike.

## File Structure

- Create: `src/mqlab/arms.py` — `Arm`, `lab_arms()`, `arm_of()`, `resolve_verb()`, `VerbImpl`.
- Create: `ansible/site-distributed-shared.yml` — the shared dtcc/app/channel plays (extracted).
- Create: `ansible/roles/mqweb/` — reusable REST-enablement role (factored from `mq-qmgr`).
- Modify: `lab/topology.yaml` — add `arms:` block; add `arm:` to each QM setup; rename `distributed`.
- Modify: `src/mqlab/setups.py` — `Setup` gains `arm: str | None`.
- Modify: `src/mqlab/cli.py` — `qm` commands dispatch via resolver; `run_setup` uses `arm_of`.
- Modify: `src/mqlab/parity.py` — retire `provisional_arm`; reconcile `MATRIX` with the registry.
- Modify: `ansible/site-distributed.yml` — becomes `import site-pcmk.yml` + `import site-distributed-shared.yml`.
- Modify: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` — apply the `mqweb` role.
- Modify: `src/mqlab/cli.py` help, `docs/reference/lab-bootstrap.md`, `lab/scripts/e2e-test.sh` — rename references.
- Test: `tests/test_arms.py`, extend `tests/test_setups.py`, `tests/test_parity.py`.

---

### Task 1: `Setup` gains an `arm` field

**Files:**
- Modify: `src/mqlab/setups.py`
- Modify: `lab/topology.yaml` (add `arm:` to setups)
- Test: `tests/test_setups.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_setups.py
from mqlab.setups import lab_setups


def test_qm_setups_declare_an_arm() -> None:
    setups = lab_setups()
    assert setups["pcmk_san_ha"].arm == "pcmk-ubuntu"
    assert setups["rdqm_ha"].arm == "rdqm-rhel"


def test_monitoring_setup_is_arm_agnostic() -> None:
    assert lab_setups()["monitoring"].arm is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_setups.py -k arm -v`
Expected: FAIL — `Setup` has no attribute `arm` (or the field isn't parsed).

- [ ] **Step 3: Add the field + parse it**

In `src/mqlab/setups.py`, add `arm` to the `Setup` dataclass (after `qm`):

```python
@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None
    secrets: list[str]
    qm: QmConfig | None
    arm: str | None
```

In `lab_setups()`, set it from the topology cfg:

```python
        result[name] = Setup(
            name=name,
            description=cfg.get("description", ""),
            groups=list(cfg.get("groups", [])),
            provision=cfg.get("provision"),
            secrets=list(cfg.get("secrets", [])),
            qm=QmConfig(
                name=cfg["qm"]["name"],
                vip=cfg["qm"]["vip"],
                vip_ext=cfg["qm"]["vip_ext"],
                dtcc_conn=cfg["qm"].get("dtcc_conn"),
            )
            if cfg.get("qm")
            else None,
            arm=cfg.get("arm"),
        )
```

In `lab/topology.yaml`, add `arm:` to each QM-bearing setup (and leave `monitoring` without one):

```yaml
  pcmk_san_ha:
    arm: pcmk-ubuntu
    # ...existing...
  pcmk_san_dr:
    arm: pcmk-ubuntu
    # ...existing...
  rdqm_ha:
    arm: rdqm-rhel
    # ...existing...
  rdqm_dr:
    arm: rdqm-rhel
    # ...existing...
```

(The `distributed` setup gets `arm: pcmk-ubuntu` here too; it is renamed in Task 7.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_setups.py -k arm -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope arms --message "setups: Setup gains an arm field; QM setups declare their arm (#<plan-a-issue>)"
```

---

### Task 2: The `arms.py` registry resolver

**Files:**
- Create: `src/mqlab/arms.py`
- Modify: `lab/topology.yaml` (add the `arms:` block)
- Test: `tests/test_arms.py`

- [ ] **Step 1: Add the `arms:` block to `lab/topology.yaml`** (top-level, near `setups:`):

```yaml
arms:
  pcmk-ubuntu:
    mechanism: pacemaker-san
    verbs:
      qm-create:  { playbook: site-pcmk-qm.yml }
      qm-destroy: { playbook: site-pcmk-qm-down.yml }
      qm-up:      { pcs: "resource enable mq_group" }
      qm-down:    { pcs: "resource disable mq_group" }
      qm-status:  { pcs: "status resources" }
  rdqm-rhel:
    mechanism: rdqm
    verbs: {}   # populated in Plan B after the verb spike
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_arms.py
from __future__ import annotations

import pytest

from mqlab.arms import VerbImpl, arm_of, lab_arms, resolve_verb


def test_lab_arms_loads_pcmk_with_verbs() -> None:
    arms = lab_arms()
    assert arms["pcmk-ubuntu"].mechanism == "pacemaker-san"
    assert "qm-create" in arms["pcmk-ubuntu"].verbs


def test_arm_of_returns_the_setups_arm() -> None:
    assert arm_of("pcmk_san_ha") == "pcmk-ubuntu"


def test_arm_of_raises_for_arm_agnostic_setup() -> None:
    with pytest.raises(ValueError, match="no arm"):
        arm_of("monitoring")


def test_resolve_verb_returns_kind_and_value() -> None:
    assert resolve_verb("pcmk_san_ha", "qm-create") == VerbImpl(kind="playbook", value="site-pcmk-qm.yml")
    assert resolve_verb("pcmk_san_ha", "qm-up") == VerbImpl(kind="pcs", value="resource enable mq_group")


def test_resolve_verb_unsupported_raises() -> None:
    with pytest.raises(KeyError, match="does not implement"):
        resolve_verb("rdqm_ha", "qm-create")  # rdqm-rhel has no verbs yet
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_arms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.arms'`.

- [ ] **Step 4: Create `src/mqlab/arms.py`**

```python
"""The arm-backend registry resolver (RDQM-parity design §2).

`topology.yaml`'s `arms:` block is the catalog: each arm names its mechanism and
maps stable verbs to their per-arm implementation. This resolver is the single
source of arm truth — it replaces P1's provisional_arm stopgap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from mqlab.paths import repo_root
from mqlab.setups import lab_setups


@dataclass(frozen=True)
class Arm:
    name: str
    mechanism: str
    verbs: dict[str, dict[str, str]]


@dataclass(frozen=True)
class VerbImpl:
    kind: str   # "playbook" | "pcs" | "cmd" | "script"
    value: str


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def lab_arms() -> dict[str, Arm]:
    arms: dict[str, Arm] = {}
    for name, cfg in (_topology().get("arms") or {}).items():
        cfg = cfg or {}
        arms[name] = Arm(name=name, mechanism=cfg.get("mechanism", ""), verbs=cfg.get("verbs") or {})
    return arms


def arm_of(setup_name: str) -> str:
    setup = lab_setups().get(setup_name)
    if setup is None or setup.arm is None:
        raise ValueError(f"setup {setup_name!r} has no arm")
    return setup.arm


def resolve_verb(setup_name: str, verb: str) -> VerbImpl:
    arm_name = arm_of(setup_name)
    arm = lab_arms()[arm_name]
    impl = arm.verbs.get(verb)
    if not impl:
        raise KeyError(f"arm {arm_name!r} does not implement verb {verb!r}")
    [(kind, value)] = impl.items()
    return VerbImpl(kind=kind, value=value)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_arms.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope arms --message "arms: data-driven registry resolver (lab_arms/arm_of/resolve_verb) (#<plan-a-issue>)"
```

---

### Task 3: Retire `provisional_arm`; point `mqlab run` + `parity.MATRIX` at the registry

**Files:**
- Modify: `src/mqlab/parity.py`
- Modify: `src/mqlab/cli.py` (`run_setup`)
- Test: `tests/test_parity.py`, `tests/test_arms.py`

- [ ] **Step 1: Write the failing test** (the registry's arms must match the capability matrix)

```python
# add to tests/test_arms.py
from mqlab.parity import MATRIX


def test_registry_arms_match_the_capability_matrix() -> None:
    # the matrix and the registry must agree on the arm set (single source of truth)
    assert set(lab_arms()) == set(MATRIX)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_arms.py -k match_the_capability -v`
Expected: PASS or FAIL depending on current `MATRIX` — if FAIL, the arm sets diverge (the bug we're fixing). Confirm both arms (`pcmk-ubuntu`, `rdqm-rhel`) exist in each; reconcile names so they match.

- [ ] **Step 3: Replace `provisional_arm` usage in `run_setup`**

In `src/mqlab/cli.py`, change the import and the call. Remove `parity.provisional_arm(setup.name)` and use the registry:

```python
from mqlab.arms import arm_of
# ...
    scenario = build_report(
        "BASELINE", arm_of(setup.name), facts, peak_exposure=peak_exposure(firm)
    )
```

- [ ] **Step 4: Delete the `provisional_arm` stopgap from `parity.py`**

Remove `_PROVISIONAL_ARM` and `provisional_arm()` from `src/mqlab/parity.py` (and their tests in `tests/test_parity.py` — `test_provisional_arm_maps_known_setups`, `test_provisional_arm_unknown_raises`). Keep `MATRIX`, `Support`, `VERBS`, `supported`, `render_markdown` (the capability matrix stays).

- [ ] **Step 5: Run tests to verify green**

Run: `uv run pytest tests/test_arms.py tests/test_parity.py tests/test_cli_run.py -v`
Expected: PASS. (`run_setup` is `# pragma: no cover`; `arm_of` is covered by `test_arms.py`.)

- [ ] **Step 6: Commit**

```bash
vrg-commit --type refactor --scope arms --message "retire P1 provisional_arm; mqlab run + parity use the registry (#<plan-a-issue>)"
```

---

### Task 4: `qm` commands dispatch via the resolver

**Files:**
- Modify: `src/mqlab/cli.py` (`_qm_playbook`/`_qm_pcs` → registry dispatch; the 5 `qm` commands)
- Test: `tests/test_cli_qm.py`

- [ ] **Step 1: Write the failing test** (the `qm` commands resolve their impl from the registry, not hardcoded strings)

```python
# add to tests/test_cli_qm.py
from mqlab.arms import resolve_verb


def test_qm_verbs_resolve_from_registry_for_pcmk() -> None:
    # the command implementations now come from the registry, proving dispatch
    assert resolve_verb("pcmk_san_ha", "qm-up").value == "resource enable mq_group"
    assert resolve_verb("pcmk_san_ha", "qm-create").value == "site-pcmk-qm.yml"
```

(Plus: keep the existing `tests/test_cli_qm.py` behavioral tests green — they assert the emitted `CommandStep`s; after the refactor they must still produce the same `ansible-playbook site-pcmk-qm.yml …` / `pcs resource enable mq_group` commands.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_qm.py -v`
Expected: the new test PASSES (it tests the registry), but this step is to confirm the existing qm tests still describe current behavior before refactor.

- [ ] **Step 3: Refactor `_qm_playbook`/`_qm_pcs` to resolve from the registry**

In `src/mqlab/cli.py`, the 5 `qm` commands stop passing hardcoded strings. Each resolves its verb impl and dispatches by `kind`. Replace the command bodies:

```python
from mqlab.arms import VerbImpl, resolve_verb

@qm_app.command("create")
def qm_create(setup: str) -> None:
    """Create the queue manager + its HA resources (arm-dispatched)."""
    _qm_dispatch(setup, "qm-create")

@qm_app.command("destroy")
def qm_destroy(setup: str) -> None:
    """Remove the queue manager + its HA resources."""
    _qm_dispatch(setup, "qm-destroy")

@qm_app.command("up")
def qm_up(setup: str) -> None:
    """Start the QM."""
    _qm_dispatch(setup, "qm-up")

@qm_app.command("down")
def qm_down(setup: str) -> None:
    """Stop the QM (HA intact)."""
    _qm_dispatch(setup, "qm-down")

@qm_app.command("status")
def qm_status(setup: str) -> None:
    """Show the QM's HA resource state."""
    _qm_dispatch(setup, "qm-status")
```

Add the dispatcher, which routes by impl kind to the existing playbook/pcs machinery:

```python
def _qm_dispatch(setup_name: str, verb: str) -> None:
    impl = resolve_verb(setup_name, verb)
    if impl.kind == "playbook":
        _qm_playbook(setup_name, impl.value, verb)
    elif impl.kind == "pcs":
        _qm_pcs(setup_name, f"pcs {impl.value}", verb)
    else:  # pragma: no cover - cmd/script kinds arrive with the RDQM backend (Plan B)
        typer.echo(f"qm verb kind {impl.kind!r} not supported yet for {setup_name}", err=True)
        raise typer.Exit(code=2)
```

`_qm_playbook` and `_qm_pcs` keep their bodies (they already take the playbook name / pcs command). Remove the now-unused `_PCMK_RESOURCE_GROUP` constant if nothing else references it (the `pcs` args now live in the registry).

- [ ] **Step 4: Run tests to verify green**

Run: `uv run pytest tests/test_cli_qm.py -v`
Expected: PASS — the emitted commands are unchanged (registry values equal the old hardcoded strings), proving a behavior-preserving refactor.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type refactor --scope qm --message "qm commands dispatch via the arm registry (no behavior change) (#<plan-a-issue>)"
```

---

### Task 5: Extract `site-distributed-shared.yml`

**Files:**
- Create: `ansible/site-distributed-shared.yml`
- Modify: `ansible/site-distributed.yml`

- [ ] **Step 1: Move the shared plays out.** Copy the `dtcc`/`app`/`mq-inter-qm` plays (everything after the `import_playbook: site-pcmk.yml` line — the cold-boot wait, the `dtcc` mq-install/mq-qmgr/mq-inter-qm plays, the `app` mq-client/app-requester plays) from `ansible/site-distributed.yml` into a new `ansible/site-distributed-shared.yml`. These plays already reference `our_qm` as a var — keep that.

- [ ] **Step 2: Reduce `site-distributed.yml` to two imports:**

```yaml
# Distributed MQ (#147), pcmk arm: the Pacemaker substrate + the shared
# DTCC/app/channel layer. The shared layer lives in site-distributed-shared.yml so
# the RDQM arm can import the same plays over its own substrate (RDQM-parity §4).
- import_playbook: site-pcmk.yml
- import_playbook: site-distributed-shared.yml
  vars:
    our_qm: QMPCMK
```

(If `our_qm` is already set inside the `mq-inter-qm` play's vars, keep it there and drop the `vars:` here — match the existing wiring; the point is the shared file is import-only and substrate-free.)

- [ ] **Step 3: Syntax-check** (the validation pipeline includes `ansible` syntax-check per #162):

Run: `vrg-container-run -- vrg-validate`
Expected: ansible syntax-check passes for both playbooks; full pipeline green.

- [ ] **Step 4: Commit**

```bash
vrg-commit --type refactor --scope ansible --message "extract site-distributed-shared.yml (substrate-free DTCC/app/channel plays) (#<plan-a-issue>)"
```

---

### Task 6: Reusable `mqweb` role; apply to the in-house pcmk QM

**Files:**
- Create: `ansible/roles/mqweb/` (tasks/main.yml, templates/ — factored from `mq-qmgr`)
- Modify: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`
- Modify: `ansible/roles/mq-qmgr/tasks/main.yml` (use the shared role — optional dedupe)

- [ ] **Step 1: Factor the `mqweb` role.** Create `ansible/roles/mqweb/` containing the mqweb enablement currently inlined in `mq-qmgr` (the `mqweb.service` systemd unit from `templates/mqweb.service.j2`, the `mqwebuser.xml` from `templates/mqwebuser.xml.j2`, `strmqweb`, and the `mqweb_admin_user`/`mqweb_admin_password` wiring). Move those templates into `ansible/roles/mqweb/templates/`. The role takes the QM/user/password vars it already uses.

- [ ] **Step 2: Apply `mqweb` to the in-house pcmk QM.** In `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`, add (after the QM is created):

```yaml
- name: enable the admin REST API on the in-house QM (every QM must expose REST — design §1)
  ansible.builtin.include_role:
    name: mqweb
```

- [ ] **Step 3: De-dupe `mq-qmgr`** (optional but DRY): replace `mq-qmgr`'s inline mqweb tasks with `include_role: name: mqweb`, so QMDTCC and the in-house QMs share one mqweb implementation.

- [ ] **Step 4: Validate** (syntax + the lab gate is Task 8):

Run: `vrg-container-run -- vrg-validate`
Expected: ansible syntax-check green.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqweb --message "factor reusable mqweb role; enable REST on the in-house pcmk QM (design §1) (#<plan-a-issue>)"
```

---

### Task 7: Rename `distributed` → `distributed-pcmk-ubuntu` + reference sweep

**Files:**
- Modify: `lab/topology.yaml` (the setup key)
- Modify: `src/mqlab/cli.py` (run help), `docs/reference/lab-bootstrap.md`, `lab/scripts/e2e-test.sh`

- [ ] **Step 1: Rename the setup key** in `lab/topology.yaml`: `distributed:` → `distributed-pcmk-ubuntu:` (keep its `arm: pcmk-ubuntu`, groups, provision, qm).

- [ ] **Step 2: Update references.**
  - `src/mqlab/cli.py` `run_setup` help: `"e.g. distributed"` → `"e.g. distributed-pcmk-ubuntu"`.
  - `docs/reference/lab-bootstrap.md`: the setups table row + any `distributed` mention.
  - `lab/scripts/e2e-test.sh`: the header comment.

- [ ] **Step 3: Sweep for stragglers**

Run: `grep -rn '\bdistributed\b' src/ lab/ docs/ ansible/ | grep -v 'distributed-pcmk-ubuntu\|distributed-shared\|site-distributed'`
Expected: no setup-name references to bare `distributed` remain (only the playbook filenames `site-distributed*.yml`, which are fine).

- [ ] **Step 4: Run the unit suite**

Run: `uv run pytest -q`
Expected: PASS (any test referencing the setup name updated).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type refactor --scope topology --message "rename distributed -> distributed-pcmk-ubuntu + reference sweep (#<plan-a-issue>)"
```

---

### Task 8: Full validation + lab acceptance gate (regression net)

**Files:** none (verification only)

- [ ] **Step 1: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: ruff + mypy + ty clean, 100% branch coverage, audit ✓, all tests pass.

- [ ] **Step 2: Lab regression net (the pcmk arm must be unchanged).** From the main `develop` checkout with the lab base VM up:

```bash
mqlab net create all
mqlab vm create distributed-pcmk-ubuntu
mqlab vm provision distributed-pcmk-ubuntu
mqlab qm create distributed-pcmk-ubuntu     # now arm-dispatched via the registry
mqlab run distributed-pcmk-ubuntu           # the P1 baseline run — MUST go green
```

Expected: `mqlab run distributed-pcmk-ubuntu` exits 0, baseline all-Confirmed, a report bundle under `build/reports/`. This proves the seam refactor + playbook extraction + rename didn't change pcmk behavior. Also confirm the in-house QM now answers REST (Task 6): `curl -sk https://<QMPCMK-vip>:9443/ibmmq/rest/v2/ ...` returns (auth per `mqweb_admin_user`).

- [ ] **Step 3: Record the cold-rebuild acceptance** per the lab's gate (the run from a clean state, one pass). Note any deviation as a finding.

---

## Out of scope for Plan A (next plans)

- **Plan B:** RDQM QM-lifecycle verb spike → fill `rdqm-rhel` registry verbs; apply `mqweb` to QMRDQM; `distributed-rdqm-rhel` setup + `site-rdqm-distributed.yml`. Acceptance: `mqlab run distributed-rdqm-rhel` green.
- **Plan C:** RDQM 3+3 DR (`crtmqm -rr` + `rdqmdr` cutover/failback), the `mqlab dr` command group, `distributed-rdqm-rhel-dr`.

## Self-Review

**1. Spec coverage:** §2 seam → Tasks 1,2,4; retire `provisional_arm`/reconcile `MATRIX` → Task 3; §3 rename + sweep → Task 7; §4 extract shared playbook → Task 5; §5 REST-on-every-QM (pcmk side) → Task 6; §8 Plan-A acceptance (`mqlab run` net + cold boot) → Task 8. The RDQM-side mqweb + verbs are correctly deferred to Plan B (this plan only does the pcmk side + the seam). No gaps for Plan A's scope.

**2. Placeholder scan:** `#<plan-a-issue>` in commit messages is the one intentional fill-in (the implementation issue number, opened at execution). No TBD/TODO in code steps; every code step shows complete code. The Ansible tasks (5,6) are structural-move steps with exact files + the syntax-check/lab gates as verification (Ansible role moves aren't unit-testable; the cold boot is their gate — stated explicitly).

**3. Type consistency:** `Setup.arm: str | None` (Task 1) consumed by `arm_of` (Task 2) and the `MATRIX` reconcile (Task 3). `VerbImpl(kind, value)` (Task 2) consumed by `_qm_dispatch` (Task 4). `resolve_verb`/`arm_of`/`lab_arms` signatures match across Tasks 2–4 and the tests. `mqweb` role name consistent across Tasks 6 and the spec.
