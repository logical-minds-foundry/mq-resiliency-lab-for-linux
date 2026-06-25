# mqlab CLI Namespace Rationalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse 9 setups + 4 arms into 4 canonical `stacks`, and replace the `net`/`vm`/`obs`/`qm` sprawl with a stack-centric, phase-based `bootstrap`/`teardown`/`status`/`commons` surface whose completion is derived from live probes (no half-states).

**Architecture:** A `Stack` model unifies arm (behavior) + setup (shape). `bootstrap <stack>` runs four idempotent phases (`net → vms → provision → observe`), each gated by a live "satisfied?" probe so re-runs resume and can't drift. Commons (OBS + svc + app, one set per host) are reference-counted. Concurrency comes from per-stack-distinct QM names (from #351) + multi-instance svc/app/exporters + per-stack-additive Prometheus targets. Reuses the existing `CommandStep`/`Command`/`run_steps`/`Deps`/`_probe_states` primitives.

**Tech Stack:** Python 3.14 (Typer, frozen dataclasses), Ansible (composable role-includes), pytest. Spec: `docs/specs/2026-06-25-mqlab-cli-namespace-design.md`.

## Global Constraints

- **Depends on #351** (QM naming) landing first — this plan consumes `Stack.qm_app`/`qm_svc`/`chl_to_svc`/`chl_to_app` and the per-stack distinct QM names. Do not start Phase D (concurrency) until #351 is merged.
- **Validation is `vrg-container-run -- vrg-validate` ONLY.** 100% branch coverage (`--cov-branch --cov-fail-under=100`).
- **Commit with `vrg-commit`**; no auto-close keywords in bodies (use `Ref #350`). Git/GitHub via `vrg-git`/`vrg-gh`.
- **Work in** `.worktrees/issue-350-cli-namespace/` on `feature/350-cli-namespace`.
- **Reuse orchestration primitives** — `CommandStep(label, Command(argv, cwd, env))`, `run_steps(steps, *, runner, renderer, transcript, step_mode, pauser)`, `build_deps(verb, ts) -> Deps`, `_probe_states(deps) -> dict[name,state]`, `classify(states, item) -> RUNNING|OFF|ABSENT`. Don't invent new ones.
- **Fail-loud:** non-zero step → `StepFailedError` → `typer.Exit`. No swallowed errors, no success-on-partial-work.
- **Four stacks:** `pcmk-ubuntu`, `rdqm-rhel`, `nativeha-rhel`, `nativeha-ubuntu` (reserved). Phases: `net`, `vms`, `provision`, `observe`.

---

## File Structure

- `lab/topology.yaml` — new `stacks:` block (4 entries); delete non-canonical `setups`; `monitoring` → `commons`; drop `pcmk-rhel` arm.
- `src/mqlab/setups.py` → `src/mqlab/stacks.py` — `Stack` model (mechanism+os+verbs+shape+qm+allocations+provision); `lab_stacks()`, `stack_members()`.
- `src/mqlab/phases.py` (new) — the phase registry: each phase has a name, a `build_steps(stack, deps) -> list[CommandStep]`, and a `satisfied(stack, states) -> bool` probe.
- `src/mqlab/cli.py` — `bootstrap`/`teardown`/`status`/`commons`/`ssh`/`qm`/`run` top-level + `--from`/`--only`; demote `net`/`vm`/`obs` groups to internal helpers.
- `ansible/site-pcmk.yml`, `site-rdqm.yml`, `site-nativeha.yml` — one full-HADR playbook per stack, composed of role-includes (`cluster-HA` + `DR-replication` + `commons-wiring`).
- `tests/test_stacks.py`, `tests/test_phases.py`, `tests/test_cli_bootstrap.py`, `tests/test_cli_teardown.py`, `tests/test_cli_status.py`, `tests/test_cli_commons.py` (new/updated).

---

## PHASE A — The Stack model

### Task 1: Unify arm+setup into `Stack`; collapse topology to 4 stacks

**Files:**
- Create: `src/mqlab/stacks.py`, `tests/test_stacks.py`
- Modify: `lab/topology.yaml` (add `stacks:`; delete the 8 non-canonical setups, `monitoring`→`commons`, drop `pcmk-rhel` arm)
- Modify: `src/mqlab/setups.py` (re-export from `stacks` for back-compat during transition, or migrate callers)

**Interfaces:**
- Produces: `@dataclass(frozen=True) Stack` with fields `name: str`, `mechanism: str`, `os: str`, `short: str`, `verbs: dict`, `groups: list[str]` (site-A + site-B + SAN), `qm: QmConfig`, `provision: str`, `secrets: list[str]`, `alloc: dict` (cluster IPs, VIPs, svc/app/exporter ports). Functions `lab_stacks() -> dict[str, Stack]`, `stack_members(name) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stacks.py
from mqlab.stacks import lab_stacks, stack_members

def test_four_canonical_stacks(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # seed topology with the 4 stacks
    stacks = lab_stacks()
    assert set(stacks) == {"pcmk-ubuntu", "rdqm-rhel", "nativeha-rhel", "nativeha-ubuntu"}
    pu = stacks["pcmk-ubuntu"]
    assert pu.mechanism == "pacemaker-san"
    assert pu.os == "ubuntu"
    assert pu.short == "PCMK"
    assert pu.qm.qm_app == "PCMKAPP"  # from #351
    assert "pcmk_a" in pu.groups and "pcmk_b" in pu.groups  # full HADR shape

def test_stack_members_flattens_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert "pcmk-a1" in stack_members("pcmk-ubuntu")
    assert "pcmk-b1" in stack_members("pcmk-ubuntu")  # site B in canonical shape
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_stacks.py -v`
Expected: FAIL — `mqlab.stacks` undefined.

- [ ] **Step 3: Add `stacks:` to `lab/topology.yaml`** (4 entries, each merging the old arm's `mechanism`/`cluster_group`/`verbs`/`short` with the full-HADR `groups` + `qm` + `alloc`), and delete the 8 non-canonical setups + `pcmk-rhel` arm; rename `monitoring` setup to a `commons:` block. Example:

```yaml
stacks:
  pcmk-ubuntu:
    mechanism: pacemaker-san
    os: ubuntu
    short: PCMK
    groups: [san_a, pcmk_a, san_b, pcmk_b]   # full HADR
    provision: ansible/site-pcmk.yml
    secrets: [pcmk_hacluster_password, mqweb_admin_password]
    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }  # name derives from short (#351)
    alloc: { exporter_app_port: 9157, exporter_svc_port: 9158, app_unit: app-pcmk, svc_port: 1414 }
    verbs: { qm-up: {pcs: "resource enable mq_group"}, ... }
  rdqm-rhel:    { mechanism: rdqm, os: rhel, short: RDQM, groups: [rdqm_a, rdqm_b], ... }
  nativeha-rhel:{ mechanism: native-ha, os: rhel, short: NHAR, groups: [nha_rhel_a, nha_rhel_b], ... }
  nativeha-ubuntu: { mechanism: native-ha, os: ubuntu, short: NHAU, groups: [], provision: null, ... }  # reserved
commons:
  groups: [obs_box, probe, svc, app]
  provision: ansible/site-obs.yml
```

- [ ] **Step 4: Implement `src/mqlab/stacks.py`** — `Stack` dataclass + `lab_stacks()`/`stack_members()` parsing the `stacks:` block (mirroring the old `lab_setups()` in `setups.py:63-99`, resolving `qm` via the #351 `short` derivation). Keep `setups.py` importing from `stacks` for any not-yet-migrated caller.

- [ ] **Step 5: Run to verify it passes**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_stacks.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add src/mqlab/stacks.py tests/test_stacks.py lab/topology.yaml src/mqlab/setups.py
vrg-commit --type feat --scope stacks --message "unify arm+setup into Stack; collapse to 4 canonical stacks" --body "Full-HADR shape per stack; delete non-canonical setups; drop pcmk-rhel; monitoring->commons. Ref #350."
```

---

## PHASE B — Playbook consolidation (highest-risk; composable role-includes)

### Task 2: One full-HADR playbook per stack

**Files:**
- Modify: `ansible/site-pcmk.yml` (absorb `site-pcmk-dr.yml` + `site-distributed.yml`), `ansible/site-rdqm.yml` (absorb `site-rdqm-dr.yml` + `site-rdqm-distributed.yml`); `ansible/site-nativeha.yml` is the template
- Delete: `site-pcmk-dr.yml`, `site-distributed.yml`, `site-rdqm-dr.yml`, `site-rdqm-distributed.yml`, the `qm-create` sub-playbooks (folded into provision)

No Python unit test (ansible; cold-rebuild gated — Task 12). **This is the highest-risk task** (per spec §7): making site B + DRBD canonical for pcmk-ubuntu may surface latent DR bugs. Build from composable role-includes so failures localize.

- [ ] **Step 1: Restructure `site-pcmk.yml` as composed includes:**

```yaml
# ansible/site-pcmk.yml — one full-HADR provision for pcmk-ubuntu
- import_playbook: _pcmk-cluster-ha.yml      # site A 3-node HA (SAN + pacemaker + QM)
- import_playbook: _pcmk-dr-replication.yml  # site B + DRBD cross-site (was site-pcmk-dr.yml)
- import_playbook: site-distributed-shared.yml  # svc/app commons wiring (was site-distributed.yml tail)
  vars:
    our_qm: "{{ setup_dict.qm.qm_app }}"     # from #351
    our_conn: "10.60.0.10(1414),10.60.0.20(1414)"
```

Extract the HA-only and DR-only bodies into the `_pcmk-cluster-ha.yml` / `_pcmk-dr-replication.yml` building blocks (move, don't rewrite). Same pattern for `site-rdqm.yml`.

- [ ] **Step 2: Fold the `qm-create` step into provision** — the inter-QM MQSC (`mq-pcmk-qmgr` / `mq-inter-qm`) runs inside provision, not as a separate `qm create` verb.

- [ ] **Step 3: Static lint** — `cd .worktrees/issue-350-cli-namespace && vrg-container-run -- vrg-validate` (ansible-lint/yamllint stages green).

- [ ] **Step 4: Commit** (cold-rebuild proof deferred to Task 12, but commit the structure)

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add ansible/
vrg-commit --type refactor --scope stacks --message "consolidate per-tech playbooks into one full-HADR provision each" --body "Composable role-includes (cluster-HA + DR + commons-wiring). Ref #350."
```

---

## PHASE C — The CLI phase sequencer (the core)

### Task 3: Phase registry with live "satisfied?" probes

**Files:**
- Create: `src/mqlab/phases.py`, `tests/test_phases.py`

**Interfaces:**
- Produces: `@dataclass Phase` with `name: str`, `build_steps: Callable[[Stack, Deps], list[CommandStep]]`, `satisfied: Callable[[Stack, dict[str,str]], bool]`. Module constant `PHASES: list[Phase]` in order `[net, vms, provision, observe]`. Function `first_unsatisfied(stack, states) -> int | None` (index of the first phase whose probe is False, or None if all satisfied).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phases.py
from mqlab.phases import PHASES, first_unsatisfied
from mqlab.stacks import lab_stacks

def test_phase_order():
    assert [p.name for p in PHASES] == ["net", "vms", "provision", "observe"]

def test_first_unsatisfied_resumes_from_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path)); _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    # vms satisfied, provision not (cluster daemons down)
    states = _fake_states(net=True, vms=True, provision=False, observe=False)
    assert first_unsatisfied(stack, states) == 2  # provision

def test_all_satisfied_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path)); _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    assert first_unsatisfied(stack, _fake_states(True, True, True, True)) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_phases.py -v`
Expected: FAIL — `mqlab.phases` undefined.

- [ ] **Step 3: Implement `phases.py`** — each phase's `satisfied` probe (per spec §5.1):
  - `net`: required networks ACTIVE (reuse `classify_net`).
  - `vms`: stack members + commons VMs `RUNNING` (reuse `_probe_states`/`classify`).
  - `provision`: cluster daemons up + QM defined — probe via the stack's `qm-status` verb (or a `cluster_daemon_up`-style check), returning bool.
  - `observe`: this stack's exporter responds + targets registered (probe Prometheus `/api/v1/targets` or the rendered target file).
  Each `build_steps` returns the `CommandStep`s for that phase (net define/autostart; `vagrant up` members+commons; `ansible-playbook <stack.provision>` with #351 vars; render targets/dashboard + `site-obs.yml`/instrument).

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_phases.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add src/mqlab/phases.py tests/test_phases.py
vrg-commit --type feat --scope phases --message "add phase registry with live satisfied-probes" --body "net/vms/provision/observe; first_unsatisfied drives resume. Ref #350."
```

---

### Task 4: `bootstrap` runs phases from the first unsatisfied; `--from`/`--only`

**Files:**
- Modify: `src/mqlab/cli.py` (`_bootstrap_run`/`bootstrap` at 1577-1600)
- Test: `tests/test_cli_bootstrap.py` (new)

**Interfaces:**
- Consumes: `PHASES`, `first_unsatisfied` (Task 3); `stack_members` (Task 1).
- Produces: `bootstrap <stack> [--from PHASE] [--only PHASE]` runs the selected phases via `run_steps`, fail-loud.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_bootstrap.py
def test_bootstrap_runs_from_first_unsatisfied(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # net+vms satisfied → bootstrap runs provision then observe
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False))
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu"])
    assert result.exit_code == 0
    labels = [s.label for s in _recorded_steps(runner)]
    assert "pcmk-ubuntu provision" in labels and "pcmk-ubuntu observe" in labels
    assert "pcmk-ubuntu net" not in labels  # satisfied → skipped

def test_bootstrap_only_runs_one_phase(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[ScriptedResult([])])))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_cli_bootstrap.py -v`
Expected: FAIL — `--from`/`--only` unknown; old `_bootstrap_run` signature.

- [ ] **Step 3: Rewrite `_bootstrap_run`** as the phase sequencer:

```python
def _bootstrap_run(stack_name, *, only=None, from_phase=None, step):
    stack = _lookup_stack_or_exit(stack_name)
    _prepare_lab()
    deps = build_deps("bootstrap", _ts())
    states = _probe_all(deps, stack)
    if only:
        selected = [p for p in PHASES if p.name == only]
    elif from_phase:
        idx = [p.name for p in PHASES].index(from_phase)
        selected = PHASES[idx:]
    else:
        start = first_unsatisfied(stack, states)
        selected = [] if start is None else PHASES[start:]
    steps = [s for p in selected for s in p.build_steps(stack, deps)]
    if not steps:
        deps.renderer.note(f"{stack_name}: already satisfied — nothing to do")
    run_steps(steps, runner=deps.runner, renderer=deps.renderer,
              transcript=deps.transcript, step_mode=step, pauser=deps.pauser)
    # on StepFailedError: print "resume with: mqlab bootstrap {stack} --from {failing-phase}"
```

Add `--from`/`--only` Typer options to `bootstrap` (validate the value is a known phase name; exit 2 otherwise). Wrap `run_steps` to catch `StepFailedError`, print the resume hint naming the failing phase, and re-raise `typer.Exit`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_cli_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add src/mqlab/cli.py tests/test_cli_bootstrap.py
vrg-commit --type feat --scope bootstrap --message "phase sequencer: resume from first unsatisfied; --from/--only" --body "Probe-derived completion, fail-loud with resume hint. Ref #350."
```

---

### Task 5: Converge prerequisite-ensures into the phases

**Files:**
- Modify: `src/mqlab/phases.py` (each phase calls `_ensure_prereqs` appropriately), `src/mqlab/cli.py`
- Test: `tests/test_phases.py`

**Interfaces:** Consumes the existing `_ensure_prereqs(name, ...)` helper. Per spec §5.4: root callback keeps `build ensure`; `vms` ensures boxes+MQ tarball; `provision` ensures galaxy+MQ+PKI; `observe` ensures exporter PKI.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phases.py
def test_provision_phase_ensures_prereqs(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr("mqlab.phases._ensure_prereqs", lambda *a, **k: calls.append(a))
    stack = lab_stacks()["pcmk-ubuntu"]
    _provision_phase().build_steps(stack, _fake_deps())
    assert calls, "provision phase must ensure prereqs"
```

- [ ] **Step 2–4: Run-fail → wire `_ensure_prereqs` into the `vms`/`provision`/`observe` `build_steps` → run-pass.** (The root callback `_root` at cli.py:302 already runs `_build_ensure` — leave it.)

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add src/mqlab/phases.py tests/test_phases.py
vrg-commit --type feat --scope phases --message "converge prerequisite-ensures into the phases" --body "vms: boxes+MQ; provision: galaxy+MQ+PKI; observe: exporter PKI. Ref #350, #307."
```

---

### Task 6: `teardown` with reference-counted commons

**Files:**
- Modify: `src/mqlab/cli.py` (add `teardown` command)
- Test: `tests/test_cli_teardown.py` (new)

**Interfaces:**
- Produces: `teardown <stack> [--commons]`. Removes the stack's cluster/SAN VMs + its commons instances (svc QM, app instance, scrape targets); destroys commons VMs only if no other stack references them, or if `--commons`.
- Consumes: `_other_stacks_up(deps, exclude) -> bool` (probe whether any other stack's members are running).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_teardown.py
def test_teardown_keeps_commons_when_other_stack_up(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel"])
    assert result.exit_code == 0
    labels = [s.label for s in _recorded_steps(runner)]
    assert not any("commons" in l and "destroy" in l for l in labels)  # kept

def test_teardown_commons_flag_forces_commons_destroy(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel", "--commons"])
    assert result.exit_code == 0
    assert any("commons" in s.label for s in _recorded_steps(runner))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_cli_teardown.py -v`
Expected: FAIL — no `teardown` command.

- [ ] **Step 3: Implement `teardown`** — build steps: destroy the stack's member VMs (reuse `_plan_destroy`), remove its commons instances (svc QM endmqm/dltmqm via the stack verb; remove its exporter unit + scrape-target entry), then conditionally destroy commons VMs based on `_other_stacks_up(deps, exclude=stack_name)` or `--commons`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd .worktrees/issue-350-cli-namespace && uv run pytest tests/test_cli_teardown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-350-cli-namespace
vrg-git add src/mqlab/cli.py tests/test_cli_teardown.py
vrg-commit --type feat --scope teardown --message "teardown with reference-counted commons" --body "Last-one-out reclaims commons; --commons forces it. Ref #350."
```

---

### Task 7: Stack-wide `status`

**Files:**
- Modify: `src/mqlab/cli.py` (`status` command), reuse `vmstatus.vm_status_core`
- Test: `tests/test_cli_status.py`

**Interfaces:** `status [<stack>]` shows, per stack: each phase's satisfied/unsatisfied, member VM states, commons health. Reuses `_probe_states` + `PHASES[i].satisfied`.

- [ ] **Step 1–4: TDD** — failing test asserts the rendered table includes a row per phase with ✓/✗ derived from each `Phase.satisfied`; implement by probing once and mapping `PHASES` → satisfied; pass.

```python
# tests/test_cli_status.py
def test_status_shows_phase_completion(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False))
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert "provision" in result.stdout and "✗" in result.stdout
```

- [ ] **Step 5: Commit** `vrg-commit --type feat --scope status --message "stack-wide status with phase completion" --body "Ref #350."`

---

### Task 8: `commons` standalone verb

**Files:**
- Modify: `src/mqlab/cli.py` (`commons` command)
- Test: `tests/test_cli_commons.py`

**Interfaces:** `commons <up|status|down>` stands up / checks / tears down the shared OBS+svc+app+probe independently. `up` reuses the obs render+create+provision steps (today's `_obs_up_steps`); `down` destroys the commons VMs; `status` shows commons health.

- [ ] **Step 1–4: TDD** — failing test asserts `commons up` runs the render + `vagrant up obs mon-probe svc app` + `site-obs.yml` steps; implement by extracting today's `_obs_up_steps()` into a commons-up builder; pass.

```python
# tests/test_cli_commons.py
def test_commons_up_provisions_shared_host(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["commons", "up"])
    assert result.exit_code == 0
    assert any("site-obs.yml" in " ".join(s.command.argv) for s in _recorded_steps(runner))
```

- [ ] **Step 5: Commit** `vrg-commit --type feat --scope commons --message "commons up/status/down standalone verb" --body "Standalone peer to teardown --commons. Ref #350."`

---

### Task 9: Demote `net`/`vm`/`obs` groups to internals

**Files:**
- Modify: `src/mqlab/cli.py` (remove `app.add_typer(... name="net"/"vm"/"obs")` registrations at 154-155, 438-442; keep the underlying functions as private helpers the phases call)
- Test: `tests/test_cli_surface.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_surface.py
def test_demoted_groups_are_not_user_facing():
    from typer.testing import CliRunner
    out = CliRunner().invoke(cli.app, ["--help"]).stdout
    for verb in ("bootstrap", "teardown", "status", "commons", "ssh", "qm", "run", "doctor", "build", "pki", "parity"):
        assert verb in out
    for gone in ("net", "vm", "obs"):
        assert gone not in out  # demoted to internals
```

- [ ] **Step 2–4: Run-fail → remove the `add_typer` registrations for `net`/`vm`/`obs` (keep `qm`); retain the functions (`net_create`, `vm_create`, `_obs_up_steps`, render helpers) as module-private callees of the phases → run-pass.** Keep `ssh` promoted to top-level (`mqlab ssh`).

- [ ] **Step 5: Commit** `vrg-commit --type refactor --scope cli --message "demote net/vm/obs groups to phase internals" --body "Ref #350."`

---

## PHASE D — Concurrency (after #351 merges)

### Task 10: PKI spike — per-stack service QMs on one host

**Files:** scratch only (a spike; no production code until findings are in).

- [ ] **Step 1:** On the cloud host, stand up two service QMs (`RDQMSVC`, `NHARSVC`) on the one shared `svc` VM, each reusing the `app-org` keystore, each on its own port. Confirm `MON.SVRCONN` SSLPEER pinning validates the exporter against both, and `dspmq` shows both QMs healthy. **Document findings** in the spec's §6 (a short "spike result" note) and commit that doc update. If pinning fails with N co-resident QMs, capture the required PKI change before Task 11.

- [ ] **Step 2: Commit the spike findings** `vrg-commit --type docs --scope concurrency --message "PKI spike: per-stack service QMs co-resident" --body "Ref #350."`

---

### Task 11: Multi-instance svc/app/exporters + per-stack-additive targets

**Files:**
- Modify: `ansible/site-obs.yml` / the `observe` phase (one exporter instance per stack on its own port; register only this stack's targets), `ansible/roles/mq-inter-qm` (per-stack svc QM instance), the app role (per-stack app unit)
- Modify: `src/mqlab/scrape.py` (`lab_scrape_targets` → per-stack additive: render only running stacks' targets), `tests/test_scrape.py`

**Interfaces:** Consumes `Stack.alloc` (exporter ports, app unit, svc port) + #351 QM names. Produces additive scrape targets (observe registers this stack's targets; teardown removes them).

- [ ] **Step 1: Write the failing test** (Python-testable part — additive targets)

```python
# tests/test_scrape.py
def test_targets_only_include_running_stacks(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr("mqlab.scrape._running_stacks", lambda: {"rdqm-rhel"})
    text = lab_scrape_targets()
    assert "RDQMAPP" in text or "10.50.0.3:9158" in text  # rdqm exporter present
    assert "nha" not in text.lower()  # nativeha-rhel not up → not registered
```

- [ ] **Step 2–4: Run-fail → make `lab_scrape_targets()` additive (per running stack, distinct exporter ports from `alloc`); make the `observe` phase add this stack's exporter unit + app instance via per-stack vars; teardown removes them → run-pass.**

- [ ] **Step 5: Commit** `vrg-commit --type feat --scope concurrency --message "multi-instance svc/app/exporters + per-stack-additive targets" --body "Ref #350, #345, #351."`

---

## PHASE E — Acceptance

### Task 12: Cold-rebuild + concurrency validation + docs

**Files:**
- Create: `docs/development/mqlab-command-reference.md` (the documented surface per spec §7)

- [ ] **Step 1: Full validation** — `cd .worktrees/issue-350-cli-namespace && vrg-container-run -- vrg-validate` green.

- [ ] **Step 2: Human-run cold rebuild of each implemented stack** (`pcmk-ubuntu`, `rdqm-rhel`, `nativeha-rhel`): `mqlab bootstrap <stack>` from scratch comes up one-pass, end-to-end, with the Grafana dashboard populated and the QM running (the bug this whole effort fixes). Re-run `mqlab bootstrap <stack>` → "already satisfied — nothing to do" (idempotent/probe-derived).

- [ ] **Step 3: Human-run concurrency acceptance (manual)** — on the cloud host, `mqlab bootstrap rdqm-rhel` then `mqlab bootstrap nativeha-rhel`; confirm both dashboards populate, no port/QM-name/IP collisions, one shared OBS scrapes both. `mqlab teardown nativeha-rhel` keeps commons up (rdqm still running); `mqlab teardown rdqm-rhel` reclaims commons.

- [ ] **Step 4: Write `mqlab-command-reference.md`** — one line per verb and phase stating exactly what it does (spec §7 deliverable).

- [ ] **Step 5: Commit** `vrg-commit --type docs --scope cli --message "command reference; bootstrap/teardown/status acceptance" --body "Ref #350."`

---

## Self-Review

**Spec coverage:** §3 stack model → Task 1; §4 phases/command surface → Tasks 3,4,7,8,9; §5.1 probe-derived completion → Tasks 3,4; §5.2 reference-counted commons → Task 6; §5.4 prereq-ensures → Task 5; §6 concurrency (multi-instance, additive targets, PKI) → Tasks 10,11; §7 playbook consolidation + docs → Tasks 2,12; §8 acceptance gates → Task 12. No gaps.

**Placeholder scan:** Python tasks (1,3,4,5,6,7,8,11-py) carry full TDD code; ansible tasks (2,11-ansible) cite exact files + the include/var changes; the PKI spike (10) is explicitly a spike with a documented-findings gate. No "TBD"/"add error handling".

**Type consistency:** `Stack`/`lab_stacks`/`stack_members` (Task 1) used in Tasks 3,4,6,7,11. `Phase`/`PHASES`/`first_unsatisfied` (Task 3) used in Tasks 4,7. `_probe_all`/`_other_stacks_up` introduced and reused consistently. `Stack.qm.qm_app` etc. match #351's interface.

**Dependency note:** Phase D (Tasks 10–11) requires #351 merged. Phases A–C can proceed in parallel with #351 (they consume the QM names only at provision/observe runtime, mocked in unit tests).
