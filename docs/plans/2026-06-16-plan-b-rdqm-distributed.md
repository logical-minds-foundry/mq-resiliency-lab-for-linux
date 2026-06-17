# Plan B — RDQM Distributed-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring RHEL/RDQM to parity with the `distributed-pcmk-ubuntu` setup — `app → QMRDQM (3-node RDQM HA) ⇄ QMDTCC over net-ext → responder → reply` — driven by the same `mqlab` command surface, by implementing the `rdqm-rhel` backend behind Plan A's arm registry.

**Architecture:** Extend Plan A's data-driven seam to a second backend. The `qm` dispatch grows two verb kinds (`cmd`, `script`) and an arm-aware cluster group; the `rdqm-rhel` arm's registry verbs are filled from a live verb spike. A new `distributed-rdqm-rhel` setup composes the RDQM HA substrate with the *same* shared distributed layer (`site-distributed-shared.yml`) the pcmk arm uses, plus the RDQM-specific pieces: a `net-ext` partner link, the our-side inter-QM MQSC to QMDTCC, and `mqweb` on QMRDQM.

**Tech Stack:** Python 3.12 (frozen dataclasses, `from __future__ import annotations`), the merged Plan A seam (`mqlab.arms`, `mqlab.cli` qm dispatch), Ansible, RHEL 9.6 x86-64 under TCG, IBM MQ Advanced for Developers + RDQM (`crtmqm -sx`/`-sxs`, `rdqmint`, `rdqmstatus`, `rdqmadm`), pytest (`uv run pytest`, 100% branch coverage).

## Global Constraints

- **Artifact gate (must hold before Task 1's lab steps):** `build/rhel-9.6-x86_64-dvd.iso` (✅ present), `build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz` (✅ present), and the **RHEL box built** via `bash lab/boxes/rhel96/build-box.sh` (the remaining prereq).
- **TCG scope-honesty (pivot §6):** RDQM is x86 under TCG — **functional correctness only**; never report or compare RTO/timing.
- **`mqlab run` is deferred (#215):** the acceptance gate is `lab/scripts/e2e-test.sh` (the `app_requester` round-trip), **not** `mqlab run`.
- **REST on every QM (design §1):** QMRDQM gets `mqweb` (the reusable `ansible/roles/mqweb` role from Plan A).
- **Disjoint per-arm resources (pivot §3.4):** the RDQM arm uses its own VIPs/nets — `QMRDQM` data VIP `10.10.1.100` (vs pcmk's `.200`), partner VIP `10.60.0.30` (vs pcmk's `.10`).
- Work in the worktree; commit with `vrg-commit`; validate with `vrg-container-run -- vrg-validate`. Lab bring-up is driven from the **main `develop` checkout** after merge (no worktree-drive — that's #167's tedium).

## File Structure

- Modify: `lab/topology.yaml` — `cluster_group` on each arm; `net-ext` on `rdqm-a1..3`; new `distributed-rdqm-rhel` setup; `rdqm-rhel` verbs filled.
- Modify: `src/mqlab/arms.py` — `Arm` gains `cluster_group`.
- Modify: `src/mqlab/cli.py` — generalize `_qm_pcs`→`_qm_cluster_cmd` (arm-aware target); `_qm_dispatch` handles `cmd`/`script`.
- Modify: `lab/scripts/rdqm-qm-create.sh` — second `rdqmint` VIP on net-ext; inter-QM MQSC when a counterparty is given.
- Create: `ansible/site-rdqm-distributed.yml` — `import site-rdqm.yml` + `import site-distributed-shared.yml` (`our_qm: QMRDQM`).
- Modify: `ansible/site-rdqm.yml` — apply the `mqweb` role on `rdqm_a`.
- Create: `docs/reports/2026-06-16-rdqm-verb-spike.md` — Task 1 findings.
- Test: `tests/test_arms.py`, `tests/test_cli_qm.py`, `tests/test_setups.py`, `tests/test_topology_integrity.py`.

---

### Task 1: RDQM QM-lifecycle verb spike (lab investigation)

**Purpose:** Determine the *real* RDQM `qm up/down/status` commands against a live HA group, so the registry rows (Task 4) are verified, not guessed (design §5). This is a lab procedure, not a code task; its output is a committed findings note.

**Prereq:** RHEL box built (`bash lab/boxes/rhel96/build-box.sh`), `/dev/kvm` present.

- [ ] **Step 1: Bring up a bare RDQM HA group** (from the main checkout once Task 5/7 land, or from here for the spike):

```bash
mqlab net create all
mqlab vm create rdqm_ha
mqlab vm provision rdqm_ha          # site-rdqm.yml: rdqm-install + rdqm-ha (forms the 3-node sync group)
bash lab/scripts/rdqm-qm-create.sh QMRDQM 10.10.1.100   # crtmqm -sxs/-sx + rdqmint + base MQSC
```

- [ ] **Step 2: Run the candidate verb commands and record exact output.** On the cluster's first node (`ansible rdqm-a1 -b -m shell -a "<cmd>"`):

```bash
# status — expected authoritative HA/DR state command:
ansible rdqm-a1 -b -m shell -a "/opt/mqm/bin/rdqmstatus -m QMRDQM"
ansible rdqm-a1 -b -m shell -a "/opt/mqm/bin/dspmq -m QMRDQM -o all"
# stop/start a managed RDQM QM (RDQM owns the QM via its bundled Pacemaker — confirm which works):
ansible rdqm-a1 -b -m shell -a "su mqm -c '/opt/mqm/bin/endmqm -w QMRDQM'"     # does RDQM restart it?
ansible rdqm-a1 -b -m shell -a "su mqm -c '/opt/mqm/bin/strmqm QMRDQM'"
ansible rdqm-a1 -b -m shell -a "/opt/mqm/bin/rdqmadm -m QMRDQM"                # cluster-level admin options
```

- [ ] **Step 3: Write `docs/reports/2026-06-16-rdqm-verb-spike.md`** recording, for each operator verb, the command that actually performs it on RDQM HA (the observed one — e.g. `rdqmstatus -m {qm}` for status), with the raw output snippet and any surprise (RDQM auto-restart behaviour, preferred-node placement). These exact strings populate Task 4's registry rows.

- [ ] **Step 4: Commit the findings**

```bash
vrg-commit --type docs --scope rdqm --message "rdqm verb spike: observed qm up/down/status on a live HA group (#216)"
```

---

### Task 2: Arm-aware cluster group (`Arm.cluster_group`); generalize `_qm_pcs`

**Files:**
- Modify: `lab/topology.yaml` (`arms:` block), `src/mqlab/arms.py`, `src/mqlab/cli.py`
- Test: `tests/test_arms.py`, `tests/test_cli_qm.py`

**Interfaces:**
- Consumes: `mqlab.arms.Arm(name, mechanism, verbs)`, `lab_arms()`, `arm_of(setup)` (Plan A).
- Produces: `Arm.cluster_group: str`; `cli._qm_cluster_cmd(setup_name: str, shell_cmd: str, verb: str)` (runs `shell_cmd` on `<arm.cluster_group>[0]`, replacing the pcmk-hardcoded `_qm_pcs`).

- [ ] **Step 1: Add `cluster_group` to the `arms:` registry** in `lab/topology.yaml`:

```yaml
arms:
  pcmk-ubuntu:
    mechanism: pacemaker-san
    cluster_group: pcmk_a
    verbs:
      # ...existing...
  rdqm-rhel:
    mechanism: rdqm
    cluster_group: rdqm_a
    verbs: {}
```

- [ ] **Step 2: Write the failing test** (`tests/test_arms.py`, extend the seeded `TOPO` with `cluster_group:` under `pcmk-ubuntu`):

```python
def test_arm_declares_its_cluster_group(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert lab_arms()["pcmk-ubuntu"].cluster_group == "pcmk_a"
```

(Add `    cluster_group: pcmk_a\n` under `pcmk-ubuntu:` in this file's `TOPO`.)

- [ ] **Step 3: Run → fail** (`uv run pytest tests/test_arms.py -k cluster_group -v` → `Arm` has no `cluster_group`).

- [ ] **Step 4: Add the field + parse it** in `src/mqlab/arms.py`:

```python
@dataclass(frozen=True)
class Arm:
    name: str
    mechanism: str
    verbs: dict[str, dict[str, str]]
    cluster_group: str = ""
```

In `lab_arms()`:

```python
        arms[name] = Arm(
            name=name,
            mechanism=cfg.get("mechanism", ""),
            verbs=cfg.get("verbs") or {},
            cluster_group=cfg.get("cluster_group", ""),
        )
```

- [ ] **Step 5: Run → pass** (`uv run pytest tests/test_arms.py -k cluster_group -v`).

- [ ] **Step 6: Generalize `_qm_pcs` → `_qm_cluster_cmd`** in `src/mqlab/cli.py`. Replace the `_qm_pcs` body's hardcoded `_PCMK_CLUSTER_GROUP` with the arm's group, and rename. The existing `_PCMK_CLUSTER_GROUP = "pcmk_a"` constant is removed.

```python
from mqlab.arms import arm_of, lab_arms, resolve_verb

def _qm_cluster_cmd(setup_name: str, shell_cmd: str, verb: str) -> None:
    # Run one streamed shell op on the arm's cluster first node (pcs for pcmk,
    # rdqm* for rdqm). No pre-flight — the cluster's own error speaks (#109).
    _setup_qm_or_exit(setup_name)
    group = lab_arms()[arm_of(setup_name)].cluster_group
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        _render_inventory(deps)
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(["ansible", f"{group}[0]", "-b", "-m", "shell", "-a", shell_cmd], cwd=repo_root() / "ansible"),  # noqa: S607
        )
        run_steps([step], runner=deps.runner, renderer=deps.renderer, transcript=deps.transcript, step_mode=False, pauser=deps.pauser)
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()
```

Update `_qm_dispatch`'s `pcs` branch to call `_qm_cluster_cmd(setup_name, f"pcs {impl.value}", verb)`.

- [ ] **Step 7: Run the qm tests → green** (the existing `test_cli_qm.py` `qm up/down/status` tests assert `ansible pcmk_a[0] … "pcs resource enable mq_group"`; the generalization preserves that for the pcmk arm because its `cluster_group` is `pcmk_a`).

Run: `uv run pytest tests/test_cli_qm.py tests/test_arms.py -v`
Expected: PASS (behaviour preserved for pcmk).

- [ ] **Step 8: Commit**

```bash
vrg-commit --type feat --scope arms --message "arms: cluster_group per arm; qm cluster ops are arm-aware (#216)"
```

---

### Task 3: `cmd` and `script` verb kinds in `_qm_dispatch`

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_qm.py`

**Interfaces:**
- Consumes: `resolve_verb` → `VerbImpl(kind, value)` (Plan A); `_qm_cluster_cmd` (Task 2); `_setup_qm_or_exit` → `QmConfig`; `mqlab.paths.lab_script`.
- Produces: `_qm_dispatch` handling `kind in {"cmd","script"}` with `{qm}`/`{vip}` substitution.

- [ ] **Step 1: Write the failing test** (`tests/test_cli_qm.py`; seed an `rdqm-rhel` arm + `rdqm_ha` setup with `cmd`/`script` verbs). Add to the file's `_ARMS` an `rdqm-rhel` block and a seeded `rdqm` setup, then:

```python
def test_qm_status_runs_rdqm_cmd_on_cluster_node(monkeypatch, tmp_path):
    topo = (
        "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "arms:\n  rdqm-rhel:\n    mechanism: rdqm\n    cluster_group: rdqm_a\n    verbs:\n"
        "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
        "      qm-create: { script: rdqm-qm-create.sh }\n"
        "setups:\n  rdqm_dist:\n    arm: rdqm-rhel\n    groups: [rdqm_a]\n"
        "    qm: { name: QMRDQM, vip: 10.10.1.100, vip_ext: 10.60.0.30 }\n"
    )
    _seed(monkeypatch, tmp_path, topo)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "rdqm_dist"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible", "rdqm_a[0]", "-b", "-m", "shell", "-a", "/opt/mqm/bin/rdqmstatus -m QMRDQM",
    ]


def test_qm_create_runs_rdqm_script_with_qm_and_vip(monkeypatch, tmp_path):
    topo = (
        "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "arms:\n  rdqm-rhel:\n    mechanism: rdqm\n    cluster_group: rdqm_a\n    verbs:\n"
        "      qm-create: { script: rdqm-qm-create.sh }\n"
        "setups:\n  rdqm_dist:\n    arm: rdqm-rhel\n    groups: [rdqm_a]\n"
        "    qm: { name: QMRDQM, vip: 10.10.1.100, vip_ext: 10.60.0.30 }\n"
    )
    _seed(monkeypatch, tmp_path, topo)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm_dist"])
    assert result.exit_code == 0
    argv = runner.recorded[-1].argv
    assert argv[0] == "bash" and argv[1].endswith("/lab/scripts/rdqm-qm-create.sh")
    assert argv[2:] == ["QMRDQM", "10.10.1.100"]
```

- [ ] **Step 2: Run → fail** (the `cmd`/`script` kinds hit the pragma stub → exit 2).

- [ ] **Step 3: Implement the kinds** in `_qm_dispatch` (`src/mqlab/cli.py`). Replace the `else` stub:

```python
def _qm_dispatch(setup_name: str, verb: str) -> None:
    qm = _setup_qm_or_exit(setup_name)
    impl = resolve_verb(setup_name, verb)
    if impl.kind == "playbook":
        _qm_playbook(setup_name, impl.value, verb)
    elif impl.kind == "pcs":
        _qm_cluster_cmd(setup_name, f"pcs {impl.value}", verb)
    elif impl.kind == "cmd":
        _qm_cluster_cmd(setup_name, impl.value.format(qm=qm.name, vip=qm.vip), verb)
    elif impl.kind == "script":
        _qm_script(setup_name, impl.value, qm, verb)
    else:  # pragma: no cover - unknown kinds are a topology error
        typer.echo(f"qm {verb}: unknown arm verb kind {impl.kind!r}", err=True)
        raise typer.Exit(code=2)
```

(`_setup_qm_or_exit` now also returns `qm` for the `cmd`/`script` substitution; the `playbook`/`pcs` branches ignore it, same as before.) Add `_qm_script`:

```python
def _qm_script(setup_name: str, script: str, qm: QmConfig, verb: str) -> None:
    # Run a lab script with the QM name + data VIP (the rdqm-qm-create contract).
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        step = CommandStep(
            f"{setup_name} {verb}",
            Command(["bash", str(lab_script(script)), qm.name, qm.vip]),
        )
        run_steps([step], runner=deps.runner, renderer=deps.renderer, transcript=deps.transcript, step_mode=False, pauser=deps.pauser)
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()
```

Ensure `from mqlab.paths import … lab_script …` and `from mqlab.setups import QmConfig` (TYPE_CHECKING) are imported.

- [ ] **Step 4: Run → pass** (`uv run pytest tests/test_cli_qm.py -v` — both new tests + the pcmk tests green).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope qm --message "qm dispatch: cmd + script verb kinds (rdqm) with {qm}/{vip} substitution (#216)"
```

---

### Task 4: Fill the `rdqm-rhel` registry verbs (from the Task 1 spike)

**Files:** Modify `lab/topology.yaml`; Test `tests/test_arms.py`

- [ ] **Step 1: Write the failing test** (`tests/test_arms.py`, seeded with the rdqm verbs):

```python
def test_rdqm_registry_resolves_status_and_create(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # _seed's TOPO must include the rdqm-rhel verbs below
    assert resolve_verb("rdqm_ha", "qm-create") == VerbImpl(kind="script", value="rdqm-qm-create.sh")
    assert resolve_verb("rdqm_ha", "qm-status").kind == "cmd"
```

(Extend the file's `TOPO`: give `rdqm-rhel` the verbs and `rdqm_ha` the `arm: rdqm-rhel`.)

- [ ] **Step 2: Run → fail** (rdqm-rhel verbs empty).

- [ ] **Step 3: Fill the real `arms.rdqm-rhel.verbs`** in `lab/topology.yaml`, using the **commands the Task-1 spike recorded** (the example below uses the expected `rdqmstatus`/`rdqmadm` mapping — replace each with the verified string from `docs/reports/2026-06-16-rdqm-verb-spike.md`):

```yaml
  rdqm-rhel:
    mechanism: rdqm
    cluster_group: rdqm_a
    verbs:
      qm-create: { script: rdqm-qm-create.sh }
      qm-status: { cmd: "/opt/mqm/bin/rdqmstatus -m {qm}" }
      qm-up:     { cmd: "su mqm -c '/opt/mqm/bin/strmqm {qm}'" }    # ← replace with the spike-verified up command
      qm-down:   { cmd: "su mqm -c '/opt/mqm/bin/endmqm -w {qm}'" } # ← replace with the spike-verified down command
```

- [ ] **Step 4: Run → pass** + the Plan-A `test_registry_arms_match_the_capability_matrix` still green (arm set unchanged).

Run: `uv run pytest tests/test_arms.py -v`

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope arms --message "arms: fill rdqm-rhel registry verbs from the verb spike (#216)"
```

---

### Task 5: `net-ext` partner link + partner VIP for the RDQM nodes

**Files:** Modify `lab/topology.yaml`, `lab/scripts/rdqm-qm-create.sh`

**Why:** The `rdqm-a1..3` nodes have no `net-ext` NIC, so QMRDQM cannot reach QMDTCC (on `net-ext 10.60.0.50`). The pcmk arm floats `mq_vip_ext` on net-ext; RDQM needs the same partner-facing floating IP.

- [ ] **Step 1: Add `net-ext` to `rdqm-a1..3`** in `lab/topology.yaml` (the `nics:` of each), e.g. `net-ext: 10.60.0.31/.32/.33`. (Disjoint from pcmk's `.51/.52/.53`.)

- [ ] **Step 2: Float a partner VIP on net-ext in `rdqm-qm-create.sh`.** After the existing data-VIP `rdqmint` (line ~17), add a second floating IP for the partner link (the `vip_ext`), accepting it as `$3`:

```bash
VIP_EXT="${3:-}"
# ...existing data-VIP rdqmint...
if [ -n "$VIP_EXT" ]; then
  IFACE_EXT=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP_EXT%.*}\\. | cut -d' ' -f1" | tail -1)
  run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_EXT -l $IFACE_EXT || true"
fi
```

(Confirm in-lab that `rdqmint` accepts a second floating IP for the same QM; if not, the spike note records the alternative.) Update `_qm_script` (Task 3) to also pass `qm.vip_ext` as the third arg.

- [ ] **Step 3: Commit** (lab-validated at Task 8):

```bash
vrg-commit --type feat --scope rdqm --message "rdqm: net-ext partner link + second rdqmint VIP for the inter-business link (#216)"
```

---

### Task 6: Our-side inter-QM MQSC to QMDTCC on RDQM

**Files:** Modify `lab/scripts/rdqm-qm-create.sh` (or add `ansible/roles/.../inter-qm.mqsc.j2` reuse)

**Why:** parity with `mq-pcmk-qmgr`, which (gated on `dtcc_conn`) defines the our-side `QREMOTE`/xmitq/`SENDER`/`RECEIVER`/`APP.REPLY` link to QMDTCC. `rdqm-qm-create.sh` defines only base objects.

- [ ] **Step 1:** Add an optional `DTCC_CONN` arg (`$4`) to `rdqm-qm-create.sh`; when set, append the inter-QM MQSC (mirror `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2` — same object set, `CONNAME($DTCC_CONN)`, our reply queue `APP.REPLY`, the `QMRDQM.QMDTCC`/`QMDTCC.QMRDQM` channels) to the `runmqsc $QM` block. Thread `qm.dtcc_conn` through `_qm_script` as `$4`.

- [ ] **Step 2:** Verify the channel/queue object set matches what `site-distributed-shared.yml`'s `mq-inter-qm` role expects on the **their** side (so the bidirectional link forms). The shared layer's `mq-inter-qm` already takes `our_qm`/`our_conn` — they must name `QMRDQM` and the rdqm partner VIP (`10.60.0.30`) for the rdqm setup (Task 7 sets these).

- [ ] **Step 3: Commit** (lab-validated at Task 8):

```bash
vrg-commit --type feat --scope rdqm --message "rdqm: our-side inter-QM MQSC to QMDTCC when a counterparty is set (#216)"
```

---

### Task 7: `distributed-rdqm-rhel` setup + provision + mqweb on QMRDQM

**Files:** Modify `lab/topology.yaml`; Create `ansible/site-rdqm-distributed.yml`; Modify `ansible/site-rdqm.yml`; Test `tests/test_setups.py`, `tests/test_topology_integrity.py`

- [ ] **Step 1: Write the failing setup test** (`tests/test_topology_integrity.py`):

```python
def test_distributed_rdqm_setup_composed():
    from mqlab.setups import lab_setups
    s = lab_setups()["distributed-rdqm-rhel"]
    assert s.arm == "rdqm-rhel"
    assert s.groups == ["rdqm_a", "dtcc", "app"]
    assert s.provision == "ansible/site-rdqm-distributed.yml"
    assert s.qm is not None and s.qm.name == "QMRDQM"
    assert s.qm.dtcc_conn == "10.60.0.50"
    assert "mqweb_admin_password" in s.secrets
```

- [ ] **Step 2: Add the setup** in `lab/topology.yaml`:

```yaml
  distributed-rdqm-rhel:
    description: Distributed MQ (RDQM arm) — app → site-A RDQM HA QM (QMRDQM) ⇄ DTCC service QM (QMDTCC) over net-ext
    arm: rdqm-rhel
    groups: [rdqm_a, dtcc, app]
    provision: ansible/site-rdqm-distributed.yml
    secrets: [mqweb_admin_password]
    qm: { name: QMRDQM, vip: 10.10.1.100, vip_ext: 10.60.0.30, dtcc_conn: 10.60.0.50 }
```

- [ ] **Step 3: Create `ansible/site-rdqm-distributed.yml`** (RDQM substrate + the same shared layer, `our_qm`/`our_conn` = QMRDQM and its partner VIP):

```yaml
# Distributed MQ (#147), RDQM arm: the RDQM HA substrate (site-rdqm.yml) + the same
# substrate-free DTCC/app/channel layer the pcmk arm uses (site-distributed-shared.yml,
# RDQM-parity §4). Our-side inter-QM MQSC is created at `mqlab qm create
# distributed-rdqm-rhel` (rdqm-qm-create.sh, gated on dtcc_conn — Task 6).
- import_playbook: site-rdqm.yml

- import_playbook: site-distributed-shared.yml
  vars:
    our_qm: QMRDQM
    our_conn: "10.60.0.30(1414)"
```

- [ ] **Step 4: Apply `mqweb` on the RDQM nodes** in `ansible/site-rdqm.yml` (after the `rdqm-install`/`rdqm-ha` plays, on `rdqm_a` — REST on QMRDQM, design §1):

```yaml
- hosts: rdqm_a
  roles: [mqweb]   # REST on every QM (design §1), per-node (spec §8.3)
```

(`site-rdqm.yml` runs at provision where `mqweb_admin_password` is injected, mirroring the pcmk arm's `site-pcmk.yml`.)

- [ ] **Step 5: Run the topology tests → green**

Run: `uv run pytest tests/test_topology_integrity.py tests/test_setups.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope rdqm --message "rdqm: distributed-rdqm-rhel setup + site-rdqm-distributed.yml + mqweb on QMRDQM (#216)"
```

---

### Task 8: Validation + lab acceptance (e2e round-trip on the RDQM arm)

**Files:** none (verification)

- [ ] **Step 1: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: ruff + mypy + ty clean, **100% branch coverage**, ansible syntax-check passes for the new playbook, audit ✓.

- [ ] **Step 2: Lab cold-boot acceptance** (from the main `develop` checkout after merge; RHEL box built):

```bash
mqlab net create all
mqlab vm create distributed-rdqm-rhel
mqlab vm provision distributed-rdqm-rhel       # site-rdqm-distributed.yml: RDQM HA + mqweb + DTCC/app shared layer
mqlab qm create distributed-rdqm-rhel          # arm-dispatched: rdqm-qm-create.sh QMRDQM 10.10.1.100 10.60.0.30 10.60.0.50
mqlab qm status distributed-rdqm-rhel          # arm-dispatched: rdqmstatus -m QMRDQM
bash lab/scripts/e2e-test.sh 5                 # app_requester → QMRDQM → QMDTCC → responder → APP.REPLY, ×5
```

Expected: `5/5 round-trips OK` — RDQM at parity with the pcmk distributed arm on the same `mqlab` surface. Confirm QMRDQM answers REST: `curl -sk https://10.10.1.100:9443/ibmmq/rest/v2/` returns. **Functional only** (TCG; no timing claims). Record per the cold-rebuild acceptance gate.

- [ ] **Step 3: Capture findings** (a short `docs/reports/` note: what worked one-pass, what needed iteration — `rdqmint` second-VIP, inter-QM bidirectional formation, mqweb-on-RDQM). These feed Plan C (3+3 DR) and any parity-harness revival (#215).

---

## Out of scope (later)

- **Plan C** — RDQM 3+3 DR: `crtmqm -rr` pairing, `rdqmdr` cutover/failback, `mqlab dr` command group, `distributed-rdqm-rhel-dr`.
- **#215** — `mqlab run`/report-corpus wiring (the e2e round-trip is the acceptance here).
- Today's remote-service-side (single-QM) changes — folded in separately by the human.

## Self-Review

**1. Spec coverage (#199 §5 + the lab realities):** RDQM verb spike → Task 1; `cmd`/`script` dispatch → Task 3; cluster_group → Task 2; rdqm registry verbs → Task 4; `distributed-rdqm-rhel` + `site-rdqm-distributed.yml` (shared layer reuse) → Task 7; mqweb on QMRDQM → Task 7; inter-QM MQSC → Task 6; net-ext partner link (gap the spec missed) → Task 5; acceptance via e2e-test (mqlab run deferred) → Task 8. No gaps.

**2. Placeholder scan:** The one deliberate fill-in is Task 4's `qm-up`/`qm-down` registry strings — explicitly "replace with the spike-verified command," which is the whole point of Task 1 (verified, not guessed, per design §5). Every Python step has complete code. The ansible/script tasks (5,6) are structural with the Task-8 cold boot as their gate (they're lab-semantics, not unit-testable) — stated as such.

**3. Type consistency:** `Arm.cluster_group` (Task 2) consumed by `_qm_cluster_cmd` (Task 2) and `_qm_dispatch` (Task 3). `VerbImpl(kind, value)` kinds `playbook|pcs|cmd|script` consistent across Tasks 2–4. `_qm_script(setup_name, script, qm, verb)` signature matches its call site and the `qm.name`/`qm.vip`/`qm.vip_ext`/`qm.dtcc_conn` fields from `setups.QmConfig`. `our_qm`/`our_conn` thread from the setup's `QmConfig` into the shared playbook (Task 7) exactly as the pcmk `site-distributed.yml` does.
