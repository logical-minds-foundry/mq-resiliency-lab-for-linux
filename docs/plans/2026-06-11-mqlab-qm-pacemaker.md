# mqlab qm — Pacemaker QM-HA lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `mqlab qm` (create/destroy/up/down/status) for the Pacemaker/SAN arm, driving a reproducible `mq-pcmk-qmgr` Ansible role that crystallizes `lab/scripts/pcmk-qm-create.sh`.

**Architecture:** `qm create`/`destroy` run a playbook (the `vm provision` pattern: members-running pre-flight → render the static inventory → `ansible-playbook` with `-e qm_name/qm_vip`); `qm up`/`down`/`status` run a single streamed `pcs` op on a cluster node via `ansible`. Per-setup QM config (`name`/`vip`) lives in topology. The role is the client-reproducible deliverable; validated by a live `qm create pcmk_san_ha`.

**Tech Stack:** Python 3.12, Typer, Rich, pytest (100% branch via `vrg-container-run -- vrg-validate`), Ansible, Pacemaker/pcs, IBM MQ.

**Spec:** `docs/specs/2026-06-11-mqlab-qm-pacemaker-design.md`

**Per-task conventions:**
- Git from the worktree: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-109-mqlab-qm`
- `vrg-commit --type <t> --scope <s> --message <m> [--body <b>]` (no auto-close keywords; `Ref #109`).
- `vrg-container-run -- vrg-validate` (the only validation; 100% branch). Respect ruff E501 (≤100) + magic-trailing-comma; mirror the existing `# noqa: S607` on `Command([...])` literals.

---

## Task 1: `Setup.qm` + topology config

**Files:** Modify `src/mqlab/setups.py`; Modify `lab/topology.yaml`; Modify `tests/test_setups.py`

- [ ] **Step 1: Failing test** — in `tests/test_setups.py`, extend `TOPO` so `pcmk_san_ha` gains a `qm:` line (after its `provision:`):
```python
    "    provision: ansible/site-pcmk.yml\n"
    "    qm: { name: QMPCMK, vip: 10.10.1.200 }\n"
```
Add:
```python
from mqlab.setups import QmConfig  # add to the existing import line


def test_lab_setups_parses_qm_config_defaulting_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    setups = lab_setups()
    assert setups["pcmk_san_ha"].qm == QmConfig(name="QMPCMK", vip="10.10.1.200")
    assert setups["rdqm_ha"].qm is None  # default
```

- [ ] **Step 2: Run — expect FAIL**: `vrg-container-run -- uv run pytest tests/test_setups.py -q`

- [ ] **Step 3: Implement** in `src/mqlab/setups.py` — add the dataclass + field:
```python
@dataclass(frozen=True)
class QmConfig:
    name: str
    vip: str


@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None
    secrets: list[str]
    qm: QmConfig | None
```
In `lab_setups()`'s `Setup(...)` add:
```python
            qm=QmConfig(name=cfg["qm"]["name"], vip=cfg["qm"]["vip"]) if cfg.get("qm") else None,
```

- [ ] **Step 4: Add the real topology config** — in `lab/topology.yaml`, add a `qm:` line to `pcmk_san_ha` (after its `provision:`):
```yaml
    qm: { name: QMPCMK, vip: 10.10.1.200 }
```

- [ ] **Step 5: Run — expect PASS**: `vrg-container-run -- uv run pytest tests/test_setups.py tests/test_topology_integrity.py -q`

- [ ] **Step 6: Commit**
```bash
vrg-git add src/mqlab/setups.py lab/topology.yaml tests/test_setups.py
vrg-commit --type feat --scope mqlab --message "add per-setup qm config (name + vip)" --body "Setup gains an optional QmConfig (name/vip); pcmk_san_ha declares QMPCMK/10.10.1.200 — the declarative source the qm verbs/role read. Ref #109."
```

---

## Task 2: `mqlab qm` verbs

**Files:** Modify `src/mqlab/cli.py`; Create `tests/test_cli_qm.py`

- [ ] **Step 1: Implement** in `src/mqlab/cli.py`.

Imports — add `QmConfig` is not needed in cli; add nothing new (uses `lab_setups`, already imported). After the `obs` section (near the other vm helpers), add the qm group + helpers + commands:
```python
qm_app = typer.Typer(help="MQ queue managers (Pacemaker-managed HA)", no_args_is_help=True)
app.add_typer(qm_app, name="qm")

# Pacemaker arm constants (generalized when the DR/RDQM arms land, #109 scope).
_PCMK_CLUSTER_GROUP = "pcmk_a"   # the cluster's inventory group; pcs runs on its first node
_PCMK_RESOURCE_GROUP = "mq_group"


def _setup_qm_or_exit(setup_name: str) -> Setup:
    setup = lab_setups().get(setup_name)
    if setup is None:
        typer.echo(f"no lab setup named {setup_name!r} — see mqlab vm status", err=True)
        raise typer.Exit(code=2)
    if setup.qm is None:
        typer.echo(f"setup {setup_name} has no qm config", err=True)
        raise typer.Exit(code=2)
    return setup


def _render_inventory(deps: Deps) -> None:
    # The static map ansible needs to reach the hosts (#101). Cheap; always fresh.
    inv = inventory_path()
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(lab_inventory())
    deps.renderer.note(f"rendered {inv}")
    deps.transcript.write(f"rendered {inv}")


def _qm_playbook(setup_name: str, playbook: str, verb: str) -> None:
    setup = _setup_qm_or_exit(setup_name)
    members = setup_members(setup_name) or []
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        down = [m for m in members if classify(_probe_states(deps), m) != RUNNING]
        if down:
            note = f"{', '.join(down)}: not running — run mqlab vm up {setup_name} first"
            deps.renderer.note(note)
            deps.transcript.write(note)
            raise typer.Exit(code=3)
        _render_inventory(deps)
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(
                ["uv", "run", "ansible-playbook", playbook,  # noqa: S607
                 "-e", f"qm_name={setup.qm.name}", "-e", f"qm_vip={setup.qm.vip}"],
                cwd=repo_root() / "ansible",
            ),
        )
        run_steps(
            [step], runner=deps.runner, renderer=deps.renderer,
            transcript=deps.transcript, step_mode=False, pauser=deps.pauser,
        )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


def _qm_pcs(setup_name: str, pcs_cmd: str, verb: str) -> None:
    _setup_qm_or_exit(setup_name)
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        _render_inventory(deps)
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(
                ["uv", "run", "ansible", f"{_PCMK_CLUSTER_GROUP}[0]",  # noqa: S607
                 "-b", "-m", "shell", "-a", pcs_cmd],
                cwd=repo_root() / "ansible",
            ),
        )
        run_steps(
            [step], runner=deps.runner, renderer=deps.renderer,
            transcript=deps.transcript, step_mode=False, pauser=deps.pauser,
        )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


@qm_app.command("create")
def qm_create(setup: str) -> None:
    """Create the queue manager + its Pacemaker HA resources (runs the role)."""
    _qm_playbook(setup, "site-pcmk-qm.yml", "qm-create")


@qm_app.command("destroy")
def qm_destroy(setup: str) -> None:
    """Remove the queue manager + its HA resources."""
    _qm_playbook(setup, "site-pcmk-qm-down.yml", "qm-destroy")


@qm_app.command("up")
def qm_up(setup: str) -> None:
    """Start the cluster-managed QM — pcs resource enable mq_group."""
    _qm_pcs(setup, f"pcs resource enable {_PCMK_RESOURCE_GROUP}", "qm-up")


@qm_app.command("down")
def qm_down(setup: str) -> None:
    """Cleanly stop the QM without tearing down HA — pcs resource disable mq_group."""
    _qm_pcs(setup, f"pcs resource disable {_PCMK_RESOURCE_GROUP}", "qm-down")


@qm_app.command("status")
def qm_status(setup: str) -> None:
    """Show the QM's HA resource state — pcs status resources."""
    _qm_pcs(setup, "pcs status resources", "qm-status")
```
Note: `Setup` is the return annotation of `_setup_qm_or_exit` — it is already importable; add `Setup` to the `from mqlab.setups import ...` line (`lab_setups, setup_members, Setup`).

- [ ] **Step 2: Tests** — create `tests/test_cli_qm.py`:
```python
from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("qm", "20260611T000000Z")),
        pauser=_NoPause(),
    )


_TOPO = (
    "nodes:\n"
    "  san-a:   {nics: {net-mgmt: 10.50.0.5}}\n"
    "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n    qm: { name: QMPCMK, vip: 10.10.1.200 }\n"
)


def _probe(states):
    lines = [" Id   Name        State", "----"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def _seed(monkeypatch, tmp_path, topo=_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def _argvs(runner):
    return [c.argv for c in runner.recorded]


def test_qm_create_runs_playbook_with_qm_extra_vars(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == [
        "uv", "run", "ansible-playbook", "site-pcmk-qm.yml",
        "-e", "qm_name=QMPCMK", "-e", "qm_vip=10.10.1.200",
    ]
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "inventory.ini").exists()


def test_qm_create_members_down_exits_3(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a1 not running
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 3
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]  # probe only, no playbook


def test_qm_create_playbook_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([], exit_code=2)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 2


def test_qm_destroy_runs_teardown_playbook(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "destroy", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[3] == "site-pcmk-qm-down.yml"


def test_qm_up_runs_pcs_enable_on_first_cluster_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "uv", "run", "ansible", "pcmk_a[0]", "-b", "-m", "shell", "-a",
        "pcs resource enable mq_group",
    ]


def test_qm_down_runs_pcs_disable(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "down", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs resource disable mq_group"


def test_qm_status_runs_pcs_status(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs status resources"


def test_qm_pcs_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=5)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk_san_ha"])
    assert result.exit_code == 5


def test_qm_unknown_setup_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["qm", "create", "nope"])
    assert result.exit_code == 2
    assert "no lab setup" in result.output


def test_qm_setup_without_qm_config_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, topo="groups:\n  g: [h]\nsetups:\n  bare:\n    groups: [g]\n")
    result = CliRunner().invoke(cli.app, ["qm", "up", "bare"])
    assert result.exit_code == 2
    assert "no qm config" in result.output
```

- [ ] **Step 3: Run — expect FAIL then PASS**: `vrg-container-run -- uv run pytest tests/test_cli_qm.py -q`

- [ ] **Step 4: Full gate**: `vrg-container-run -- vrg-validate` (100% branch).

- [ ] **Step 5: Commit**
```bash
vrg-git add src/mqlab/cli.py tests/test_cli_qm.py
vrg-commit --type feat --scope mqlab --message "add mqlab qm (create/destroy/up/down/status) for the Pacemaker arm" --body "create/destroy run the QM playbook (members-running pre-flight -> render inventory -> ansible-playbook with qm_name/qm_vip); up/down/status run a single streamed pcs op on the cluster's first node. Unknown setup / no qm config -> exit 2; members down -> exit 3; failures propagate. Ref #109."
```

---

## Task 3: the `mq-pcmk-qmgr` role + playbooks

**Files:** Create `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`; Create `ansible/site-pcmk-qm.yml`; Create `ansible/site-pcmk-qm-down.yml`

This crystallizes `lab/scripts/pcmk-qm-create.sh` into a declarative, idempotent role — the client-reproducible deliverable. It is **validated live** (Task 5), not pytest-unit-tested.

- [ ] **Step 1: `ansible/site-pcmk-qm.yml`**
```yaml
# Create the Pacemaker-managed queue manager + its HA resource group.
# Driven by `mqlab qm create <setup>` (passes qm_name / qm_vip).
- hosts: pcmk_a
  become: true
  vars:
    first_node: "{{ groups['pcmk_a'][0] }}"
  roles: [mq-pcmk-qmgr]
```

- [ ] **Step 2: `ansible/roles/mq-pcmk-qmgr/tasks/main.yml`** — the six task groups, mirroring the script. Creation steps run on `first_node` (`run_once`); the definition is taught to the others via `hostvars`:
```yaml
# Crystallized from lab/scripts/pcmk-qm-create.sh (#109). The hand-built spelling
# of RDQM's `crtmqm -sx` + `rdqmint`: QM on the shared LUN, taught to every node,
# handed to Pacemaker as fs -> vip -> qm. Idempotent (guards on every step).

# 1. QM on the LUN (creation node only; Pacemaker owns the mount afterward).
- name: mount the shared LUN (creation node)
  ansible.builtin.shell: >
    mkdir -p /mqshared && (mountpoint -q /mqshared ||
    mount /dev/disk/by-label/MQSHARED /mqshared) &&
    mkdir -p /mqshared/qmgrs /mqshared/log && chown -R mqm:mqm /mqshared
  run_once: true
  changed_when: false

- name: create the queue manager on the LUN
  ansible.builtin.command:
    cmd: su mqm -c '/opt/mqm/bin/crtmqm -md /mqshared/qmgrs -ld /mqshared/log {{ qm_name }}'
  run_once: true
  register: crtmqm
  failed_when: crtmqm.rc != 0 and 'already exists' not in (crtmqm.stderr | default(''))
  changed_when: crtmqm.rc == 0

# 2. MQSC config (lab posture, spec 1), then a clean stop.
- name: apply MQSC config
  ansible.builtin.shell: |
    su mqm -c '/opt/mqm/bin/strmqm {{ qm_name }}' || true
    printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\n
    START LISTENER(L1414)\n
    DEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('"'"'mqm'"'"') HBINT(15) KAINT(15) REPLACE\n
    ALTER QMGR CHLAUTH(DISABLED) CONNAUTH('"'"' '"'"')\n
    REFRESH SECURITY TYPE(CONNAUTH)\n
    DEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc {{ qm_name }}'
    su mqm -c '/opt/mqm/bin/endmqm -w {{ qm_name }}'
  run_once: true
  changed_when: true

# 3. Teach the other nodes the QM definition, then release the mount.
- name: capture the addmqinf command from the creation node
  ansible.builtin.shell: "su mqm -c '/opt/mqm/bin/dspmqinf -o command {{ qm_name }}' | grep '^addmqinf'"
  run_once: true
  register: addmqinf_cmd
  changed_when: false

- name: teach the QM definition to the other nodes
  ansible.builtin.command:
    cmd: "su mqm -c '{{ hostvars[groups['pcmk_a'][0]].addmqinf_cmd.stdout }}'"
  when: inventory_hostname != groups['pcmk_a'][0]
  register: addmqinf
  failed_when: addmqinf.rc != 0 and 'already' not in (addmqinf.stderr | default(''))
  changed_when: addmqinf.rc == 0

- name: release the LUN on the creation node (Pacemaker owns it now)
  ansible.builtin.command: umount /mqshared
  run_once: true
  failed_when: false
  changed_when: true

# 4. Disabled systemd unit on every node (Pacemaker is the only starter).
- name: queue-manager systemd unit (disabled)
  ansible.builtin.copy:
    dest: "/etc/systemd/system/mq-{{ qm_name }}.service"
    content: |
      [Unit]
      Description=IBM MQ queue manager {{ qm_name }} (pacemaker-managed)
      [Service]
      Type=forking
      User=mqm
      ExecStart=/opt/mqm/bin/strmqm {{ qm_name }}
      ExecStop=/opt/mqm/bin/endmqm -w {{ qm_name }}
      TimeoutStartSec=300
  notify: daemon-reload

- name: ensure the unit is disabled (cluster-only start)
  ansible.builtin.systemd:
    name: "mq-{{ qm_name }}.service"
    enabled: false
    daemon_reload: true

# 5. Resource stickiness — no auto-failback (the #64 lesson).
- name: resource stickiness (no auto-failback)
  ansible.builtin.command: pcs resource defaults update resource-stickiness=1000
  run_once: true
  changed_when: true

# 6. The resource group: fs -> vip -> qm.
- name: mq_fs resource (LUN, OCF_CHECK_LEVEL=20, fence on I/O failure)
  ansible.builtin.shell: >
    pcs resource status mq_fs >/dev/null 2>&1 ||
    pcs resource create mq_fs ocf:heartbeat:Filesystem
    device=/dev/disk/by-label/MQSHARED directory=/mqshared fstype=xfs
    op monitor interval=20s timeout=40s OCF_CHECK_LEVEL=20 on-fail=fence --group mq_group
  run_once: true
  changed_when: true

- name: mq_vip resource (floating IP)
  ansible.builtin.shell: >
    pcs resource status mq_vip >/dev/null 2>&1 ||
    pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip={{ qm_vip }} cidr_netmask=24
    --group mq_group --after mq_fs
  run_once: true
  changed_when: true

- name: mq_qm resource (the queue manager)
  ansible.builtin.shell: >
    pcs resource status mq_qm >/dev/null 2>&1 ||
    pcs resource create mq_qm systemd:mq-{{ qm_name }} --group mq_group --after mq_vip
  run_once: true
  changed_when: true
```
Add `ansible/roles/mq-pcmk-qmgr/handlers/main.yml`:
```yaml
- name: daemon-reload
  ansible.builtin.systemd:
    daemon_reload: true
```

- [ ] **Step 3: `ansible/site-pcmk-qm-down.yml`** (teardown)
```yaml
# Remove the Pacemaker-managed QM + its HA resources. Driven by `mqlab qm destroy`.
- hosts: pcmk_a
  become: true
  tasks:
    - name: delete the resource group
      ansible.builtin.command: pcs resource delete mq_group
      run_once: true
      failed_when: false
      changed_when: true

    - name: stop + delete the queue manager
      ansible.builtin.shell: >
        su mqm -c '/opt/mqm/bin/endmqm -i {{ qm_name }}' 2>/dev/null;
        su mqm -c '/opt/mqm/bin/dltmqm {{ qm_name }}' 2>/dev/null;
        su mqm -c '/opt/mqm/bin/rmvmqinf {{ qm_name }}' 2>/dev/null; true
      changed_when: true

    - name: remove the systemd unit
      ansible.builtin.file:
        path: "/etc/systemd/system/mq-{{ qm_name }}.service"
        state: absent
```

- [ ] **Step 4: Lint parse** — `vrg-container-run -- uv run python -c "import yaml,glob; [list(yaml.safe_load_all(open(f))) for f in glob.glob('ansible/site-pcmk-qm*.yml')]; print('parse OK')"`

- [ ] **Step 5: Commit**
```bash
vrg-git add ansible/roles/mq-pcmk-qmgr ansible/site-pcmk-qm.yml ansible/site-pcmk-qm-down.yml
vrg-commit --type feat --scope ansible --message "crystallize the Pacemaker QM-HA procedure into mq-pcmk-qmgr role" --body "Declarative, idempotent role + create/teardown playbooks, mirroring pcmk-qm-create.sh: QM on the LUN -> teach the nodes -> disabled systemd units -> stickiness -> mq_fs/mq_vip/mq_qm group. The client-reproducible artifact; validated live. Ref #109."
```

---

## Task 4: docs

**Files:** Modify `docs/site/docs/getting-started.md`

- [ ] **Step 1:** After the `vm provision` paragraph, add a `mqlab qm` section: the verb table (create via the `mq-pcmk-qmgr` role, up/down = `pcs enable/disable mq_group`, status, destroy), that it runs *after* `vm provision`, that `up`/`down` go through Pacemaker (the systemd units are disabled), and that the streamed task output *is* the reproducible step-by-step. Note RDQM + the experiments are later layers.

- [ ] **Step 2: Commit**
```bash
vrg-git add docs/site/docs/getting-started.md
vrg-commit --type docs --scope site --message "document mqlab qm (Pacemaker QM-HA lifecycle)" --body "getting-started covers qm create/destroy/up/down/status, the role-as-deliverable, and the cluster-mediated up/down. Ref #109."
```

---

## Task 5: full validate + live acceptance + PR

- [ ] **Step 1: Full gate** — `vrg-container-run -- vrg-validate` (green, 100% branch).

- [ ] **Step 2: Live (operator)** — on a provisioned `pcmk_san_ha` cluster:
```bash
mqlab vm up pcmk_san_ha && mqlab vm provision pcmk_san_ha   # infra must exist first
mqlab qm create pcmk_san_ha    # watch the role build the QM + HA group, step by step
mqlab qm status pcmk_san_ha    # mq_group Started on a node
mqlab qm down pcmk_san_ha && mqlab qm up pcmk_san_ha   # cluster-mediated stop/start
mqlab qm create pcmk_san_ha    # re-run: idempotent, converges
```
Expected: the QM comes up under Pacemaker, the `mq_group` shows `Started`, down/up toggle it cleanly. This is where the role is refined against reality (the deep-understanding pass) — fix the role and re-run until clean; commit any fixes.

- [ ] **Step 3: Engage the oracle** (issue-implement no-audit) — from the worktree:
```bash
vrg-pr-workflow next --issue 109 --no-audit
vrg-pr-workflow report-ready --title "feat(mqlab): mqlab qm — Pacemaker QM-HA lifecycle (create/destroy/up/down/status)" \
  --summary "Crystallize the Pacemaker QM-HA procedure into a reproducible mq-pcmk-qmgr role driven by mqlab qm; create/destroy via playbook, up/down via direct pcs resource enable/disable, per-setup qm: config." \
  --notes "Pacemaker arm only (Ubuntu-first); RDQM and the experiment layer are deferred. CLI tested via the CommandRunner seam at 100% branch; the role validated by a live qm create pcmk_san_ha."
vrg-pr-workflow next     # -> done: approved
```
Then tell the human: **run `vrg-submit-pr`**.

---

## Self-Review

**Spec coverage:** §2 verbs → Tasks 2,3; §3 qm config → Task 1; §4 role (6 task groups) → Task 3; §5 idempotency/pre-flight → Task 2 (members-down exit 3) + Task 3 (idempotent role); §6 components → Tasks 1,2,3; §7 data flow → Task 2; §8 error table (2/3/passthrough/0) → Task 2 tests; §9 transparency → Task 3 (streamed) + Task 4; §10 testing → Tasks 1,2 + live Task 5; §11 scope → all. Covered.

**Type/name consistency:** `QmConfig(name, vip)` and `Setup.qm: QmConfig | None` (Tasks 1,2); `_setup_qm_or_exit -> Setup`, `_render_inventory`, `_qm_playbook`, `_qm_pcs` (Task 2); constants `_PCMK_CLUSTER_GROUP="pcmk_a"`, `_PCMK_RESOURCE_GROUP="mq_group"` used consistently in code + tests; playbook names `site-pcmk-qm.yml` / `site-pcmk-qm-down.yml` consistent across Tasks 2,3.

**Placeholder scan:** none — full code in every code step; the role is a complete first cut refined live in Task 5 (explicitly, not a placeholder).
