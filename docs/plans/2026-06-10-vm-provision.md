# mqlab vm provision — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `mqlab vm provision <setup>` — a state-aware verb that auto-sources the setup's lab secrets, renders the static inventory, and runs the setup's Ansible playbook (streamed).

**Architecture:** Reuses #99's `_probe_states`/`classify` for the pre-flight and #101's `lab_inventory`/`inventory_path` for the inventory render. Secrets ride on the playbook subprocess via a new optional `Command.env` (no global `os.environ` mutation), sourced silently from `lab-secret.sh` so values never reach screen or transcript.

**Tech Stack:** Python 3.12, Typer, Rich, pytest (100% branch via `vrg-container-run -- vrg-validate`), Ansible, libvirt.

**Spec:** `docs/specs/2026-06-10-vm-provision-design.md`

**Per-task conventions:**
- Git from the worktree: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-102-vm-provision`
- `vrg-commit --type <t> --scope <s> --message <m> [--body <b>]` (no auto-close keywords; `Ref #102`).
- `vrg-container-run -- vrg-validate` (the only validation; 100% branch). Respect ruff E501 (≤100) and the magic-trailing-comma rule; mirror the existing `# noqa: S607` on `Command([...])` literals.

---

## Task 1: `Setup.secrets`

**Files:** Modify `src/mqlab/setups.py`; Modify `tests/test_setups.py`

- [ ] **Step 1: Add the failing test** (append to `tests/test_setups.py`; extend `TOPO` first)

In the `TOPO` string, add a `secrets:` line to the `pcmk_san_ha` setup:
```python
    "    provision: ansible/site-pcmk.yml\n"
    "    secrets: [pcmk_hacluster_password]\n"
```
Then add:
```python
def test_lab_setups_parses_secrets_defaulting_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    setups = lab_setups()
    assert setups["pcmk_san_ha"].secrets == ["pcmk_hacluster_password"]
    assert setups["rdqm_ha"].secrets == []  # default empty
```

- [ ] **Step 2: Run — expect FAIL** (`Setup` has no `secrets`)

Run: `vrg-container-run -- uv run pytest tests/test_setups.py -q`

- [ ] **Step 3: Implement** in `src/mqlab/setups.py`

Add the field to the dataclass:
```python
@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None
    secrets: list[str]
```
And in `lab_setups()`'s `Setup(...)` construction add:
```python
            secrets=list(cfg.get("secrets", [])),
```

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_setups.py -q`

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/setups.py tests/test_setups.py
vrg-commit --type feat --scope mqlab --message "add Setup.secrets" --body "Setups declare the lab secrets their playbook needs; default empty. Ref #102."
```

---

## Task 2: `Command.env` passthrough

**Files:** Modify `src/mqlab/runner.py`; Modify `tests/test_runner.py`

- [ ] **Step 1: Add failing tests** to `tests/test_runner.py`

```python
def test_subprocess_runner_passes_command_env():
    lines: list[str] = []
    SubprocessRunner().run(
        Command(["sh", "-c", "echo $MQLAB_TEST_VAR"], env={"MQLAB_TEST_VAR": "xyz"}),
        lines.append,
    )
    assert lines == ["xyz"]


def test_subprocess_runner_merges_command_env_over_os_environ(monkeypatch):
    monkeypatch.setenv("MQLAB_BASE", "base")
    lines: list[str] = []
    SubprocessRunner().run(
        Command(["sh", "-c", "echo $MQLAB_BASE $MQLAB_EXTRA"], env={"MQLAB_EXTRA": "extra"}),
        lines.append,
    )
    assert lines == ["base extra"]  # inherited PATH/etc preserved, command.env added
```

- [ ] **Step 2: Run — expect FAIL** (`Command` has no `env`)

Run: `vrg-container-run -- uv run pytest tests/test_runner.py -q`

- [ ] **Step 3: Implement** in `src/mqlab/runner.py`

Add `import os` after `import subprocess`. Add the field:
```python
@dataclass(frozen=True)
class Command:
    """One command to run: argv, an optional working directory, and optional
    extra environment (merged over os.environ for the child — used to inject
    lab secrets onto a single subprocess, #102)."""

    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None
```
In `SubprocessRunner.run`, compute and pass the environment:
```python
        cwd = str(command.cwd) if command.cwd is not None else None
        env = {**os.environ, **command.env} if command.env else None
        process = subprocess.Popen(  # noqa: S603 - trusted internal argv; lab tool (spec §1)
            command.argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
```
`display()` is unchanged — it shows argv only, so an env-carrying command never prints its secret.

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_runner.py -q`

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/runner.py tests/test_runner.py
vrg-commit --type feat --scope mqlab --message "let a Command carry extra subprocess env" --body "Optional Command.env merged over os.environ for the child; display() stays argv-only so secrets never print. Enables per-subprocess secret injection without global mutation. Ref #102."
```

---

## Task 3: secret-source helper + `_provision` flow + `vm provision` command

**Files:** Modify `src/mqlab/cli.py`; Modify `tests/test_cli_vm.py`

`_source_secret` runs `lab-secret.sh <name>` and captures the printed value to
inject it. No hiding: it echoes + tees like any other step (the lab is a throwaway
illusion; these auto-generated secrets carry no weight).

- [ ] **Step 1: Add imports** to `src/mqlab/cli.py` top-level imports

```python
from mqlab.inventory import inventory_path, lab_inventory
from mqlab.setups import lab_setups, setup_members
```
(If `vm_inventory` imports `inventory_path`/`lab_inventory` locally, remove that
local import now that they are top-level.)

- [ ] **Step 2: Add the tests** to `tests/test_cli_vm.py`

```python
_PCMK_TOPO = (
    "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n    secrets: [pcmk_hacluster_password]\n"
)


def test_vm_provision_sources_secret_renders_inventory_runs_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult(["s3cr3t"]),  # lab-secret.sh
            ScriptedResult([]),          # ansible-playbook
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == [*_VIRSH, "list", "--all"]
    assert argvs[1][0] == "bash" and argvs[1][2] == "pcmk_hacluster_password"
    assert "lab-secret.sh" in argvs[1][1]
    play = runner.recorded[-1]
    assert play.argv == ["uv", "run", "ansible-playbook", "site-pcmk.yml"]
    assert str(play.cwd).endswith("/ansible")
    assert play.env == {"PCMK_HACLUSTER_PASSWORD": "s3cr3t"}  # secret injected on the subprocess
    assert (tmp_path / "build" / "inventory.ini").read_text().startswith("[san_a]")


def test_vm_provision_members_down_advises_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a1 not created
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 3
    assert [c.argv for c in runner.recorded] == [[*_VIRSH, "list", "--all"]]  # probe only


def test_vm_provision_no_secret_setup_runs_playbook_without_sourcing(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "setups:\n  rdqm_ha:\n    groups: [rdqm_a]\n    provision: ansible/site-rdqm.yml\n"
    )
    runner = RecordingRunner(results=[_probe({"rdqm-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "rdqm_ha"])
    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs == [[*_VIRSH, "list", "--all"], ["uv", "run", "ansible-playbook", "site-rdqm.yml"]]
    assert runner.recorded[-1].env is None  # no secrets -> no injected env


def test_vm_provision_unknown_setup_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text("setups: {}\n")
    result = CliRunner().invoke(cli.app, ["vm", "provision", "nope"])
    assert result.exit_code == 2
    assert "no lab setup" in result.output


def test_vm_provision_setup_without_playbook_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "groups:\n  g: [h]\nsetups:\n  bare:\n    groups: [g]\n"
    )
    result = CliRunner().invoke(cli.app, ["vm", "provision", "bare"])
    assert result.exit_code == 2
    assert "no provision playbook" in result.output


def test_vm_provision_lab_secret_failure_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([], exit_code=1)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 2


def test_vm_provision_playbook_failure_propagates_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult(["s"]),
            ScriptedResult([], exit_code=4),  # ansible fails
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 4
```

- [ ] **Step 3: Run — expect FAIL** (no `provision` command)

Run: `vrg-container-run -- uv run pytest tests/test_cli_vm.py -q`

- [ ] **Step 4: Implement** in `src/mqlab/cli.py`

First the secret-source helper (near `_probe_states`) — echoes + tees like a
normal step, capturing the printed value to return it:
```python
def _source_secret(deps: Deps, name: str) -> str:
    # Auto-generated lab secret -> its value, to inject into the playbook env.
    # No hiding: the lab is a throwaway illusion, so this echoes + tees like any
    # other step (mirrors _probe_states). lab-secret.sh generates+persists once.
    cmd = Command(["bash", str(lab_script("lab-secret.sh")), name])  # noqa: S607
    deps.renderer.command(cmd.display())
    deps.transcript.write(f"$ {cmd.display()}")
    captured: list[str] = []

    def sink(line: str) -> None:
        deps.renderer.output(line)
        deps.transcript.write(line)
        captured.append(line)

    code = deps.runner.run(cmd, sink)
    if code != 0:
        deps.renderer.error(f"lab-secret.sh {name} failed (exit {code})")
        raise typer.Exit(code=2)
    return "\n".join(captured).strip()
```

Then the flow (uses `os.path.basename` to avoid a `Path` import):
```python
def _provision(setup_name: str) -> None:
    setup = lab_setups().get(setup_name)
    if setup is None:
        typer.echo(f"no lab setup named {setup_name!r} — see mqlab vm status", err=True)
        raise typer.Exit(code=2)
    if setup.provision is None:
        typer.echo(f"setup {setup_name} has no provision playbook", err=True)
        raise typer.Exit(code=2)
    members = setup_members(setup_name) or []
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("vm-provision", timestamp)
    try:
        states = _probe_states(deps)
        down = [m for m in members if classify(states, m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab vm up {setup_name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        secret_env = {s.upper(): _source_secret(deps, s) for s in setup.secrets}
        inv = inventory_path()
        inv.parent.mkdir(parents=True, exist_ok=True)
        inv.write_text(lab_inventory())
        deps.renderer.note(f"rendered {inv}")
        deps.transcript.write(f"rendered {inv}")
        step = CommandStep(
            f"{setup_name} provision",
            Command(
                ["uv", "run", "ansible-playbook", os.path.basename(setup.provision)],  # noqa: S607
                cwd=repo_root() / "ansible",
                env=secret_env or None,
            ),
        )
        run_steps(
            [step],
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=False,
            pauser=deps.pauser,
        )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()
```
Add the command (after `vm_inventory`):
```python
@vm_app.command("provision")
def vm_provision(setup: str) -> None:
    """Provision a setup — render the inventory, then run its Ansible playbook."""
    _provision(setup)
```

- [ ] **Step 5: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_cli_vm.py -q`

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_vm.py
vrg-commit --type feat --scope mqlab --message "add mqlab vm provision <setup>" --body "Probe members -> pre-flight (down -> advise mqlab vm up, exit 3) -> source declared secrets onto the playbook subprocess -> render static inventory -> run the setup's playbook (streamed). Unknown setup / no playbook -> exit 2; ansible failure propagates. Ref #102."
```

---

## Task 4: topology declarations + mqweb username constant

**Files:** Modify `lab/topology.yaml`; Modify `ansible/group_vars/all.yml`; Modify `tests/test_topology_integrity.py`

- [ ] **Step 1: Declare secrets in `lab/topology.yaml`**

Add `secrets: [pcmk_hacluster_password]` to `pcmk_san_ha` and `pcmk_san_dr`, and
`secrets: [mqweb_admin_password]` to `standalone` (after each setup's
`provision:` line).

- [ ] **Step 2: Make the mqweb username a constant** in `ansible/group_vars/all.yml`

```yaml
# MQWeb admin: username is a fixed lab constant; the password is an auto-generated
# lab secret (lab-secret.sh mqweb_admin_password), injected by `mqlab vm provision`
# as MQWEB_ADMIN_PASSWORD. Never committed.
mqweb_admin_user: mqadmin
mqweb_admin_password: "{{ lookup('env', 'MQWEB_ADMIN_PASSWORD') }}"
```

- [ ] **Step 3: Guard the real topology's secrets** — add to `tests/test_topology_integrity.py`

```python
def test_pcmk_setups_declare_the_hacluster_secret():
    setups = lab_setups()
    assert setups["pcmk_san_ha"].secrets == ["pcmk_hacluster_password"]
    assert setups["pcmk_san_dr"].secrets == ["pcmk_hacluster_password"]
    assert setups["standalone"].secrets == ["mqweb_admin_password"]
```

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_topology_integrity.py -q`

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/topology.yaml ansible/group_vars/all.yml tests/test_topology_integrity.py
vrg-commit --type feat --scope lab --message "declare per-setup secrets; mqweb username constant" --body "pcmk setups declare pcmk_hacluster_password; standalone declares mqweb_admin_password. mqweb_admin_user becomes the constant mqadmin in group_vars (only the password is generated/injected). Ref #102."
```

---

## Task 5: full validate + live + PR template

- [ ] **Step 1: Full gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green, 100% branch, all suites pass.

- [ ] **Step 2: Live provision (operator)** — from the worktree, members up first

```bash
mqlab vm up standalone        # ensure the setup's members are running
mqlab vm provision standalone # render inventory, source mqweb secret, run site.yml — watch Ansible
```
Expected: inventory renders, `MQWEB_ADMIN_PASSWORD` is sourced (it streams like
any step — no hiding), `ansible-playbook site.yml` runs to completion. Then a
re-run is safe (Ansible idempotent). The console password is also retrievable any
time: `lab/scripts/lab-secret.sh mqweb_admin_password`.

- [ ] **Step 3: PR template** — write `.vergil/pr-template.yml.tmp`, then `mv` into place

```yaml
issue: 102
title: "feat(mqlab): mqlab vm provision <setup> — state-aware Ansible provisioning"
summary: Provision a setup — pre-flight on member state, auto-source its lab secrets, render the static inventory, run its playbook (streamed).
notes: |
  The provisioning layer of the vm namespace. Pre-flight probes member state and,
  if any member is not running, advises `mqlab vm up <setup>` and exits 3
  (precondition not met — distinct from the exit-2 input errors). All lab secrets
  are auto-generated via lab-secret.sh and injected onto the playbook subprocess
  via the new Command.env (no global mutation). No secret-hiding — the lab is a
  throwaway illusion. mqweb_admin_user is now the constant `mqadmin`; only the
  password is generated. One-way (teardown is vm down/destroy); re-runnable via
  Ansible idempotency. vrg-validate green (100% branch). Closes the layer #101
  unblocked.
```

- [ ] **Step 4: Push**

```bash
vrg-git push -u origin feature/102-vm-provision
```
Tell the human: ready for `vrg-submit-pr --finalize`.

---

## Self-Review

**Spec coverage:** §2 setup-only arg → Task 3 (unknown-setup exit 2); §3.1 resolve/no-playbook → Task 3; §3.2 probe + members-down exit 3 → Task 3; §3 secrets auto-source → Task 3; §4 declarations + mqweb constant → Task 4; §5 no-hiding secret handling → Task 3 (`_source_secret` echoes+tees like any step); §6 components (`Setup.secrets`, `Command.env`, `_source_secret`, `_provision`, group_vars) → Tasks 1,2,3,4; §7 exit codes (0/2/3/passthrough) → Task 3 tests; §8 testing → all; §9 scope (group_vars in, playbooks out) → Task 4. All covered.

**Type/name consistency:** `Setup.secrets: list[str]` (Tasks 1,4); `Command.env: dict[str,str]|None` (Task 2, used Task 3); `_source_secret(deps, name) -> str` and `_provision(setup_name)` (Task 3); reused `_probe_states`, `classify`, `RUNNING`, `lab_inventory`, `inventory_path`, `setup_members`, `lab_setups` — all already exist on `develop`.

**Placeholder scan:** none — every code step is complete; live steps name exact commands + expected output.
