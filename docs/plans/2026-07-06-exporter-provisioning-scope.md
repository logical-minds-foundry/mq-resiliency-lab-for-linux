# Scope exporter provisioning to the deployed stack — implementation plan

> **Execution:** built inline in the #503 session (single task/PR). TDD per task;
> `vrg-container-run -- vrg-validate` is the gate.

**Goal:** The per-stack `observe` phase provisions only *that* stack's `mq_prometheus`
exporter (plus the shared `svc`), so un-provisioned stacks get no exporter unit and
cannot crash-loop.

**Architecture:** `mq_exporter_instances(topo)` already tags each entry with its `stack`
(`"commons"` for the shared svc). Add a `stack` filter and thread it through the render
and the CLI, and have the observe phase pass `--stack <name>`. Prometheus scrape-target
files stay full-topology (a down target is benign; those files are global).

**Tech Stack:** Python (mqlab, pytest, 100% branch gate), Typer CLI, Ansible (no role change).

## Global Constraints

- No behaviour change to the node/`ibmmq` scrape-target renders — only the exporter
  *deployment* list (`mq_exporters`) is scoped.
- The shared `svc` (stack `"commons"`) exporter is included in every scoped result.
- `stack=None` preserves the current full-topology list (the un-scoped default).
- 100% branch coverage; `vrg-validate` is the only validation.

---

### Task 1: scope `mq_exporter_instances` (pure)

**Files:**
- Modify: `src/mqlab/scrape.py` — add `stack` param to `mq_exporter_instances`,
  `render_mq_exporters`, `lab_mq_exporters`.
- Test: `tests/test_scrape.py`

**Interfaces (produces):**
- `mq_exporter_instances(topo: dict, stack: str | None = None) -> list[dict]`
- `render_mq_exporters(topo: dict, stack: str | None = None) -> str`
- `lab_mq_exporters(stack: str | None = None) -> str`

- [ ] **Step 1 — failing test.** In `tests/test_scrape.py`, using the existing topology
  fixture (or a minimal one with ≥2 stacks + a `svc:` block):

```python
def test_mq_exporter_instances_scoped_to_one_stack(topo):
    scoped = mq_exporter_instances(topo, stack="pcmk-ubuntu")
    stacks = {e["stack"] for e in scoped}
    assert stacks == {"pcmk-ubuntu", "commons"}      # this stack's app + shared svc only
    assert all(e["stack"] != "rdqm-rhel" for e in scoped)

def test_mq_exporter_instances_unscoped_is_full(topo):
    assert mq_exporter_instances(topo, stack=None) == mq_exporter_instances(topo)
```

- [ ] **Step 2 — run, expect fail** (`stack` param not accepted / not filtered).
- [ ] **Step 3 — implement.** Build the full list as today, then filter:

```python
def mq_exporter_instances(topo, stack=None):
    out = []
    # ... existing per-stack app + _svc_exporter_instance(topo) build, unchanged ...
    if stack is not None:
        out = [e for e in out if e["stack"] in (stack, "commons")]
    return out
```

  Thread the param through the wrappers:

```python
def render_mq_exporters(topo, stack=None):
    return json.dumps({"mq_exporters": mq_exporter_instances(topo, stack)}, indent=2) + "\n"

def lab_mq_exporters(stack=None):
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_mq_exporters(topo, stack)
```

- [ ] **Step 4 — run tests, expect pass.**
- [ ] **Step 5 — commit** (`vrg-commit --type feat --scope obs`).

---

### Task 2: `obs targets --stack` + observe-phase wiring

**Files:**
- Modify: `src/mqlab/cli.py` — `obs targets` gains `--stack`; only the `mq_exporters`
  render is scoped (node + `ibmmq` renders unchanged).
- Modify: `src/mqlab/phases.py` — `_observe_build_steps` passes `--stack <stack.name>`.
- Test: `tests/test_cli_obs.py`, and the observe-phase command test
  (`grep -rn "obs.*targets\|_observe_build_steps\|render targets" tests/` to find it).

**Interfaces (consumes):** `lab_mq_exporters(stack)` from Task 1.

- [ ] **Step 1 — failing test (CLI).** In `tests/test_cli_obs.py`, assert `obs targets
  --stack pcmk-ubuntu` writes an `mq_exporters` file whose entries are only
  `{pcmk-ubuntu, commons}`, while the node/`ibmmq` target files are unchanged
  (full-topology). Model it on the existing `obs targets` test.

- [ ] **Step 2 — failing test (phase).** Assert `_observe_build_steps(stack, deps)`'s
  render-targets step is `["mqlab", "obs", "targets", "--stack", stack.name]` (extend
  the existing observe-phase command assertion).

- [ ] **Step 3 — run, expect fail.**

- [ ] **Step 4 — implement CLI.** Add the option and scope only the exporter render:

```python
@obs_app.command("targets")
def obs_targets(stack: str | None = typer.Option(None, "--stack")) -> None:
    # node + ibmmq renders unchanged (full topology); scope only the mq_exporters render
    ...
    text = lab_mq_exporters(stack) if path_fn is mq_exporters_path else renderer_fn()
```

  (Rework the render loop so the `mq_exporters_path` entry uses `lab_mq_exporters(stack)`;
  the other two entries call their `renderer_fn()` unchanged.)

- [ ] **Step 5 — implement phase.** In `_observe_build_steps`, change the render step to
  `Command(["mqlab", "obs", "targets", "--stack", stack.name])`.

- [ ] **Step 6 — run tests, expect pass; fix any exact-command bootstrap assertions.**

- [ ] **Step 7 — commit.**

## Self-review

- **Spec coverage:** scoped `mq_exporter_instances` (T1) · `--stack` render + observe
  wiring (T2) · scrape targets left full-topology (T2, unchanged) · testing (each task).
- **No role change** — `site-obs.yml`'s create-loop over a shorter list makes fewer units.
- **No placeholders**; signatures consistent (`stack: str | None = None`) across tasks.
- Teardown removal + the app-requester coupling are out of scope (spec).
