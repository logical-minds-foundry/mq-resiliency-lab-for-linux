# Static Topology-Derived Inventory + Unified Grouping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the Ansible inventory deterministically from `lab/topology.yaml`, unify the grouping namespace across topology + playbooks + mqlab, and pin the SSH transport so the inventory is a pure function of the static config.

**Architecture:** A new pure renderer (`src/mqlab/inventory.py`) projects topology → INI. `topology.yaml` gains a `groups:` block (atomic role×site groups); `setups:` become group compositions with underscore names that double as Ansible `:children` parent groups. A dedicated host-only `net-mgmt` network plus `config.ssh.insert_key = false` make the SSH transport static. `mqlab vm inventory` writes/echoes `build/inventory.ini`; `inventory.sh` is retired.

**Tech Stack:** Python 3.12, Typer, Rich, pytest (100% branch via `vrg-container-run -- vrg-validate`), libvirt/vagrant-libvirt, Ansible.

**Spec:** `docs/specs/2026-06-10-static-topology-inventory-design.md`

**Conventions for every task:**
- Git from inside the worktree: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-101-static-inventory`
- Commit with `vrg-commit --type <t> --scope <s> --message <m> [--body <b>]` (never auto-close keywords; use `Ref #101`).
- Validate with `vrg-container-run -- vrg-validate` (the ONLY validation command; 100% branch required).
- New strings/lines must respect ruff E501 (≤100 cols) and the magic-trailing-comma rule.

---

## Task 1: Spike — prove the static SSH transport (GO/NO-GO gate)

**Operational, in the live lab. Run before trusting the transport in anger.** This de-risks the whole premise (spec §11) before the full rebuild.

- [ ] **Step 1: Add a minimal `net-mgmt` network and start it**

Create `lab/networks/net-mgmt.xml`:

```xml
<!-- lab/networks/net-mgmt.xml — operator/Ansible management net.
     Host-only: no <forward>, no DHCP. Kept off the modeled
     data/heartbeat/SAN planes so management traffic never pollutes the model. -->
<network>
  <name>net-mgmt</name>
  <bridge name="virbr-mgmt"/>
  <ip address="10.50.0.1" netmask="255.255.255.0"/>
</network>
```

Run (live lab): `mqlab net up net-mgmt`
Expected: the network defines + starts; `virsh -c qemu:///system net-list --all` shows `net-mgmt active`.

- [ ] **Step 2: Pin the key + give ONE guest a mgmt NIC**

In `lab/Vagrantfile`, immediately after the `config.vm.synced_folder` line, add:

```ruby
  # All guests share Vagrant's well-known insecure key so the rendered inventory
  # carries one constant key path instead of N scraped per-machine paths (#101).
  config.ssh.insert_key = false
```

Temporarily add a `net-mgmt` NIC to `pcmk-a1` in `lab/topology.yaml` (`nics:`):
`net-mgmt: 10.50.0.51`.

Run (live lab): `mqlab vm destroy pcmk-a1 && mqlab vm create pcmk-a1`
Expected: guest rebuilds; `virsh domiflist lab_pcmk-a1` lists the `net-mgmt` interface.

- [ ] **Step 3: Hand-write a 1-host inventory and ping**

Write `/tmp/spike-inv.ini`:

```ini
[spike]
pcmk-a1 ansible_host=10.50.0.51
[all:vars]
ansible_user=vagrant
ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key
ansible_ssh_common_args=-o StrictHostKeyChecking=no
```

Run (live lab, from `ansible/`): `ansible spike -i /tmp/spike-inv.ini -m ping`
Expected: `pcmk-a1 | SUCCESS => {"ping": "pong"}`.

- [ ] **Step 4: Record the GO/NO-GO**

Write `docs/reports/2026-06-10-net-mgmt-transport-spike.md` with the outcome and the **confirmed key path** (the `insecure_private_key` location actually used — if it differs from `~/.vagrant.d/insecure_private_key`, that exact path becomes the renderer constant in Task 2).

- **GO** → proceed; revert the temporary single-NIC edit (Task 9 adds NICs to all nodes).
- **NO-GO** → stop; the transport approach is re-examined before further code.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/networks/net-mgmt.xml lab/Vagrantfile docs/reports/2026-06-10-net-mgmt-transport-spike.md
vrg-commit --type feat --scope lab --message "add net-mgmt network + pin shared SSH key (spike GO)" --body "Host-only management network and config.ssh.insert_key=false; spike proves shared-key SSH over net-mgmt reaches a guest. Ref #101."
```

---

## Task 2: Inventory renderer — happy path

**Files:**
- Create: `src/mqlab/inventory.py`
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from mqlab.inventory import render_inventory

TOPO = {
    "nodes": {
        "san-a": {"nics": {"net-mgmt": "10.50.0.5"}},
        "pcmk-a1": {"nics": {"net-mgmt": "10.50.0.51"}},
        "pcmk-a2": {"nics": {"net-mgmt": "10.50.0.52"}},
    },
    "groups": {
        "san_a": ["san-a"],
        "pcmk_a": ["pcmk-a1", "pcmk-a2"],
    },
    "setups": {
        "pcmk_san_ha": {"groups": ["san_a", "pcmk_a"], "provision": "ansible/site-pcmk.yml"},
    },
}


def test_render_emits_groups_children_and_vars_in_order():
    out = render_inventory(TOPO)
    assert out == (
        "[san_a]\n"
        "san-a ansible_host=10.50.0.5\n"
        "[pcmk_a]\n"
        "pcmk-a1 ansible_host=10.50.0.51\n"
        "pcmk-a2 ansible_host=10.50.0.52\n"
        "[pcmk_san_ha:children]\n"
        "san_a\n"
        "pcmk_a\n"
        "[all:vars]\n"
        "ansible_user=vagrant\n"
        "ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key\n"
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no\n"
        "ansible_python_interpreter=/usr/bin/python3\n"
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_inventory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.inventory'`.

- [ ] **Step 3: Implement the renderer**

```python
"""Render the Ansible inventory as a pure function of lab/topology.yaml (#101).

The inventory is the full declarative *map* of every host — not a snapshot of what
is running (liveness lives in the verb pre-flight, #99/#102). One source of truth
(topology), one grouping namespace: atomic role×site groups, plus setups rendered
as Ansible `:children` parent groups (underscore names = groups, hyphens = hosts).
"""

from __future__ import annotations

# Confirmed by the Task-1 spike. With config.ssh.insert_key=false every guest
# shares Vagrant's insecure key, so the inventory needs one constant path.
INSECURE_KEY = "~/.vagrant.d/insecure_private_key"


class InventoryError(RuntimeError):
    """topology.yaml cannot be rendered to a valid inventory."""


def _mgmt_ip(nodes: dict, host: str) -> str:
    spec = nodes.get(host)
    if spec is None:
        raise InventoryError(f"group references undefined host: {host}")
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise InventoryError(f"host has no net-mgmt IP: {host}")
    return ip


def render_inventory(topo: dict) -> str:
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    setups = topo.get("setups", {})
    lines: list[str] = []
    for group, hosts in groups.items():
        lines.append(f"[{group}]")
        for host in hosts:
            lines.append(f"{host} ansible_host={_mgmt_ip(nodes, host)}")
    for setup, cfg in setups.items():
        members = (cfg or {}).get("groups", [])
        for g in members:
            if g not in groups:
                raise InventoryError(f"setup {setup} references undefined group: {g}")
        lines.append(f"[{setup}:children]")
        lines.extend(members)
    lines += [
        "[all:vars]",
        "ansible_user=vagrant",
        f"ansible_ssh_private_key_file={INSECURE_KEY}",
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no",
        "ansible_python_interpreter=/usr/bin/python3",
    ]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_inventory.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/inventory.py tests/test_inventory.py
vrg-commit --type feat --scope mqlab --message "add topology-to-inventory renderer (happy path)" --body "Pure render_inventory(topo) -> INI: atomic groups with per-host ansible_host (net-mgmt IP), setups as :children parents, shared-key [all:vars]. Ref #101."
```

---

## Task 3: Inventory renderer — fail-loud validation

**Files:**
- Modify: `tests/test_inventory.py`

The implementation in Task 2 already raises; this task locks the behavior with tests (every `raise` path must be covered for 100% branch).

- [ ] **Step 1: Add failing tests**

```python
import pytest

from mqlab.inventory import InventoryError


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"san-a": {"nics": {}}}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(InventoryError, match="no net-mgmt IP: san-a"):
        render_inventory(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(InventoryError, match="undefined host: san-a"):
        render_inventory(topo)


def test_setup_referencing_undefined_group_raises():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "setups": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(InventoryError, match="undefined group: nope"):
        render_inventory(topo)
```

- [ ] **Step 2: Run — expect PASS** (impl already raises)

Run: `vrg-container-run -- uv run pytest tests/test_inventory.py -q`
Expected: PASS (3 new tests green).

- [ ] **Step 3: Commit**

```bash
vrg-git add tests/test_inventory.py
vrg-commit --type test --scope mqlab --message "cover inventory renderer fail-loud paths" --body "Missing net-mgmt IP, undefined host, undefined group all raise InventoryError. Ref #101."
```

---

## Task 4: `setups.py` — groups model

**Files:**
- Modify: `src/mqlab/setups.py`
- Modify: `tests/test_setups.py`

`Setup` carries `groups` (not raw `members`); `setup_members` flattens via a new `lab_groups()`. `guestsel`/`fleet` route through `setup_members`, so flattening here propagates.

- [ ] **Step 1: Update the test fixture + assertions to the new schema**

Replace the `TOPO` and member assertions in `tests/test_setups.py`:

```python
TOPO = (
    "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  pcmk-a2: {}\n  rdqm-a1: {}\n"
    "groups:\n"
    "  san_a: [san-a]\n"
    "  pcmk_a: [pcmk-a1, pcmk-a2]\n"
    "  rdqm_a: [rdqm-a1]\n"
    "setups:\n"
    "  pcmk_san_ha:\n"
    "    description: Pacemaker SAN HA\n"
    "    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "  rdqm_ha:\n"
    "    groups: [rdqm_a]\n"
)
```

Update assertions:

```python
def test_lab_setups_parses_groups_description_and_provision(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    setups = lab_setups()
    assert setups["pcmk_san_ha"].groups == ["san_a", "pcmk_a"]
    assert setups["pcmk_san_ha"].description == "Pacemaker SAN HA"
    assert setups["pcmk_san_ha"].provision == "ansible/site-pcmk.yml"
    assert setups["rdqm_ha"].provision is None


def test_setup_members_flattens_groups_in_order(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert setup_members("pcmk_san_ha") == ["san-a", "pcmk-a1", "pcmk-a2"]
    assert setup_members("nope") is None
```

Add a `lab_groups` import + test:

```python
from mqlab.setups import lab_groups, lab_setups, setup_members, setups_of


def test_lab_groups_reads_atomic_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert lab_groups()["pcmk_a"] == ["pcmk-a1", "pcmk-a2"]
```

Update `test_setups_of_*` to expect membership via groups (san-a is in `pcmk_san_ha`).

- [ ] **Step 2: Run — expect FAIL**

Run: `vrg-container-run -- uv run pytest tests/test_setups.py -q`
Expected: FAIL (`Setup` has no `groups`; `lab_groups` undefined).

- [ ] **Step 3: Implement**

Rewrite `src/mqlab/setups.py`:

```python
@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None


def _topology() -> dict:
    return yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())


def lab_groups() -> dict[str, list[str]]:
    """Atomic inventory groups -> member hosts, from topology.yaml."""
    return {g: list(hosts) for g, hosts in (_topology().get("groups") or {}).items()}


def lab_setups() -> dict[str, Setup]:
    """All named setups from topology.yaml, keyed by name."""
    data = _topology()
    result: dict[str, Setup] = {}
    for name, cfg in (data.get("setups") or {}).items():
        cfg = cfg or {}
        result[name] = Setup(
            name=name,
            description=cfg.get("description", ""),
            groups=list(cfg.get("groups", [])),
            provision=cfg.get("provision"),
        )
    return result


def setup_members(name: str) -> list[str] | None:
    """Members of a setup (groups flattened in declared order, de-duped), or None."""
    setup = lab_setups().get(name)
    if setup is None:
        return None
    groups = lab_groups()
    members: list[str] = []
    for g in setup.groups:
        for host in groups.get(g, []):
            if host not in members:
                members.append(host)
    return members


def setups_of(guest: str) -> list[str]:
    """Names of the setups a guest belongs to, sorted."""
    return sorted(name for name in lab_setups() if guest in (setup_members(name) or []))
```

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_setups.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/setups.py tests/test_setups.py
vrg-commit --type feat --scope mqlab --message "model setups as group compositions" --body "Setup.groups replaces raw members; setup_members flattens via lab_groups; add lab_groups accessor. Ref #101."
```

---

## Task 5: `fleet.py` — membership via groups

**Files:**
- Modify: `src/mqlab/fleet.py:56-58` (the `setups_for` closure)
- Test: `tests/test_fleet.py`

`fleet_rows` currently computes a guest's setups with `guest in s.members`. `Setup` no longer has `members`; route through `setups_of`.

- [ ] **Step 1: Update the test fixture to the new schema**

In `tests/test_fleet.py`, change the seeded topology's `setups:` to use `groups:` (mirroring Task 4's fixture) and keep the same expected `Setup(s)` column output.

- [ ] **Step 2: Run — expect FAIL** (`Setup` has no `members`)

Run: `vrg-container-run -- uv run pytest tests/test_fleet.py -q`

- [ ] **Step 3: Implement**

In `src/mqlab/fleet.py`, replace the `setups_for` closure with a call to `setups_of`:

```python
from mqlab.setups import setups_of

def fleet_rows(platforms: dict[str, str], states: dict[str, str]) -> list[FleetRow]:
    rows = [
        FleetRow(
            guest, platform, states.get(f"lab_{guest}", "not created"),
            ", ".join(setups_of(guest)),
        )
        for guest, platform in platforms.items()
    ]
    return sorted(rows, key=lambda r: (r.setups, r.guest))
```

Remove the now-unused `lab_setups` import if no longer referenced.

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_fleet.py -q`

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/fleet.py tests/test_fleet.py
vrg-commit --type refactor --scope mqlab --message "resolve fleet setup membership via groups" --body "fleet_rows uses setups_of (group-flattened membership) instead of the removed Setup.members. Ref #101."
```

---

## Task 6: `mqlab vm inventory` command

**Files:**
- Modify: `src/mqlab/inventory.py` (add `lab_inventory()` + `inventory_path()`)
- Modify: `src/mqlab/cli.py` (add the command after `vm_status`, ~line 330)
- Test: `tests/test_cli_vm.py`

- [ ] **Step 1: Write the failing test**

```python
def test_vm_inventory_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "setups:\n  pcmk_san_ha: {groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "inventory"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "inventory.ini").read_text()
    assert "[san_a]" in written
    assert "san-a ansible_host=10.50.0.5" in written
    assert "[pcmk_san_ha:children]" in written
```

- [ ] **Step 2: Run — expect FAIL** (no `inventory` command)

Run: `vrg-container-run -- uv run pytest tests/test_cli_vm.py::test_vm_inventory_writes_and_echoes -q`

- [ ] **Step 3: Implement**

Add to `src/mqlab/inventory.py`:

```python
import yaml

from mqlab.paths import repo_root


def inventory_path() -> Path:
    return repo_root() / "build" / "inventory.ini"


def lab_inventory() -> str:
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_inventory(topo)
```

(Add `from pathlib import Path` to the imports.)

Add the command to `src/mqlab/cli.py` (after `vm_status`):

```python
@vm_app.command("inventory")
def vm_inventory() -> None:
    """Render build/inventory.ini from topology and echo it (the static map)."""
    from mqlab.inventory import inventory_path, lab_inventory

    deps = build_deps("vm-inventory", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_inventory()
        path = inventory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()
```

- [ ] **Step 4: Run — expect PASS**

Run: `vrg-container-run -- uv run pytest tests/test_cli_vm.py -q`

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/inventory.py src/mqlab/cli.py tests/test_cli_vm.py
vrg-commit --type feat --scope mqlab --message "add mqlab vm inventory command" --body "Render build/inventory.ini from topology and echo it (treatment-A transparency). Ref #101."
```

---

## Task 7: Rewrite `lab/topology.yaml` to the unified schema

**Files:**
- Modify: `lab/topology.yaml`

- [ ] **Step 1: Add a `net-mgmt` NIC to every node**

To each node's `nics:` map, add `net-mgmt: 10.50.0.N` per the spec §5.2 table
(qm-main .10, svc-sim .50, app-client .60, rdqm-a1/2/3 .31/.32/.33,
rdqm-b1/2/3 .41/.42/.43, san-a .5, pcmk-a1/2/3 .51/.52/.53, san-b .6,
pcmk-b1/2/3 .61/.62/.63).

- [ ] **Step 2: Add the `groups:` block** (before `setups:`)

```yaml
groups:                       # atomic role × site groups — the shared namespace
  san_a:  [san-a]
  san_b:  [san-b]
  pcmk_a: [pcmk-a1, pcmk-a2, pcmk-a3]
  pcmk_b: [pcmk-b1, pcmk-b2, pcmk-b3]
  rdqm_a: [rdqm-a1, rdqm-a2, rdqm-a3]
  rdqm_b: [rdqm-b1, rdqm-b2, rdqm-b3]
  qm:     [qm-main]
  svc:   [svc-sim]
  client: [app-client]
```

- [ ] **Step 3: Convert `setups:` to underscore compositions**

```yaml
setups:
  pcmk_san_ha:
    description: Ubuntu Pacemaker/Corosync + iSCSI SAN — site-A 3-node HA
    groups: [san_a, pcmk_a]
    provision: ansible/site-pcmk.yml
  pcmk_san_dr:
    description: Pacemaker/SAN 3+3 — site-A HA plus cross-site DR (DRBD async)
    groups: [san_a, san_b, pcmk_a, pcmk_b]
    provision: ansible/site-pcmk-dr.yml
  rdqm_ha:
    description: RDQM/RHEL shared-nothing — site-A 3-node HA
    groups: [rdqm_a]
    provision: ansible/site-rdqm.yml
  rdqm_dr:
    description: RDQM 3+3 — site-A HA plus cross-site DR (rdqmdr async)
    groups: [rdqm_a, rdqm_b]
    provision: ansible/site-rdqm.yml
  standalone:
    description: Phase-B standalone QM + SVC sim + client (message path)
    groups: [qm, svc, client]
    provision: ansible/site.yml
```

Update the `setups:` header comment (drop the stale "inventory.sh will derive its
groups" line; note groups are now explicit and the inventory renders from here).

- [ ] **Step 4: Validate the file parses + renders**

Run: `vrg-container-run -- uv run python -c "from mqlab.inventory import lab_inventory; print(lab_inventory())"`
Expected: prints a complete INI with every group, every host's `ansible_host`, and all five `:children` parents — no `InventoryError`.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/topology.yaml
vrg-commit --type feat --scope lab --message "unify grouping namespace; add net-mgmt IPs" --body "Add atomic groups: block; setups become underscore group-compositions; every node gains a net-mgmt IP. topology is now the single source for the inventory. Ref #101."
```

---

## Task 8: Real-topology integrity test

**Files:**
- Create: `tests/test_topology_integrity.py`

Tests seed fake topologies elsewhere; this one loads the **real** `lab/topology.yaml` so schema drift fails in CI.

- [ ] **Step 1: Write the test**

```python
from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.setups import lab_groups, lab_setups


def test_real_topology_renders_without_error():
    out = lab_inventory()  # raises InventoryError on any integrity problem
    assert "[all:vars]" in out
    assert out.endswith("ansible_python_interpreter=/usr/bin/python3\n")


def test_every_setup_group_is_defined():
    groups = lab_groups()
    for setup in lab_setups().values():
        for g in setup.groups:
            assert g in groups, f"{setup.name} references undefined group {g}"
```

- [ ] **Step 2: Run — expect PASS** (Task 7 produced a valid file)

Run: `vrg-container-run -- uv run pytest tests/test_topology_integrity.py -q`

- [ ] **Step 3: Commit**

```bash
vrg-git add tests/test_topology_integrity.py
vrg-commit --type test --scope lab --message "guard real topology renders to a valid inventory" --body "Load the real lab/topology.yaml; assert it renders and every setup group is defined. Ref #101."
```

---

## Task 9: Rewrite playbook `hosts:` targets + role references

**Files:**
- Modify: `ansible/site-pcmk.yml`, `ansible/site-pcmk-dr.yml`, `ansible/site.yml`
- Audit: `ansible/roles/**`

`site-rdqm.yml` is unchanged (its `rdqm_a`/`rdqm_b`/`rdqm_a:rdqm_b` targets are already valid atomic groups).

- [ ] **Step 1: Apply the `hosts:` rewrites** per spec §7:
  - `site-pcmk.yml`: `pcmk_a:san_hosts` → `pcmk_san_ha`; `san-a` → `san_a`.
  - `site-pcmk-dr.yml`: `pcmk_a:pcmk_b:san_hosts` → `pcmk_san_dr`; `san-a`/`san-b` → `san_a`/`san_b`.
  - `site.yml`: `all` → `standalone`; `qm_hosts`/`client_hosts` → `qm`/`client`; `qm-main`/`svc-sim` → `qm`/`svc`; `svc-sim:app-client` → `svc:client`.

- [ ] **Step 2: Grep roles for stale group names (correctness-critical)**

Run: `grep -rnE "san_hosts|qm_hosts|client_hosts" ansible/roles ansible/group_vars`
For each hit (e.g. `groups['san_hosts']` in a template), update to the new name
(`san_hosts` → `san_a`/`san_a:san_b` as appropriate to that reference's intent).
A missed reference fails loud at playbook time — that is acceptable but we fix
known ones now.

- [ ] **Step 3: Lint the playbooks parse**

Run: `vrg-container-run -- uv run python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('ansible/site*.yml')]"`
Expected: no exception.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/site-pcmk.yml ansible/site-pcmk-dr.yml ansible/site.yml ansible/roles ansible/group_vars
vrg-commit --type refactor --scope ansible --message "target unified group names in playbooks" --body "Rewrite hosts: lines to the unified namespace (setups as groups, underscore role groups); update role-internal group references. site-rdqm unchanged. Ref #101."
```

---

## Task 10: Retire `inventory.sh`; update callers + docs

**Files:**
- Delete: `ansible/inventory.sh`
- Modify: `lab/scripts/dr-provision.sh:12`
- Modify: `docs/site/docs/getting-started.md`

- [ ] **Step 1: Repoint `dr-provision.sh`**

Replace the `"$HERE/../../ansible/inventory.sh"` line with:
`mqlab vm inventory` (renders `build/inventory.ini` that `ansible.cfg` already targets).

- [ ] **Step 2: Delete the script**

Run: `vrg-git rm ansible/inventory.sh`

- [ ] **Step 3: Grep for any other caller**

Run: `grep -rn "inventory.sh" . --include=*.sh --include=*.md --include=*.yml`
Update any remaining reference to `mqlab vm inventory`.

- [ ] **Step 4: Update the walkthrough**

In `docs/site/docs/getting-started.md`: rename setup tokens to underscore form
(`pcmk-san-ha` → `pcmk_san_ha`, etc.), and add `mqlab vm inventory` to the `vm`
command list with a one-line note that the inventory is rendered from topology.

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/inventory.sh lab/scripts/dr-provision.sh docs/site/docs/getting-started.md
vrg-commit --type refactor --scope lab --message "retire inventory.sh for the static renderer" --body "Delete vagrant-scraping inventory.sh; dr-provision.sh and docs use mqlab vm inventory; getting-started uses underscore setup names. Ref #101."
```

---

## Task 11: Full validation + live bring-up

**Files:** none (verification)

- [ ] **Step 1: Full gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green, 100% branch coverage, all suites pass.

- [ ] **Step 2: Live — networks + rebuild**

Run (live lab): `mqlab net up all` then rebuild one full setup, e.g.
`mqlab vm destroy pcmk_san_ha && mqlab vm create pcmk_san_ha`
Expected: guests come up with their `net-mgmt` NICs.

- [ ] **Step 3: Live — inventory + ping**

Run: `mqlab vm inventory` (inspect the echoed INI)
Then (from `ansible/`): `ansible pcmk_san_ha -m ping`
Expected: every member returns `pong` over the static `net-mgmt` transport.

- [ ] **Step 4: Write the PR template (the "done" signal)**

Write `.vergil/pr-template.yml.tmp` then `mv` into place:

```yaml
issue: 101
title: "feat(lab): static topology-derived inventory + unified grouping namespace"
summary: Render the Ansible inventory deterministically from topology; one grouping namespace across topology, playbooks, and mqlab; pinned net-mgmt SSH transport.
notes: |
  inventory.sh's vagrant-scraping is retired for a pure render_inventory(topo).
  topology gains a groups: block; setups become underscore group-compositions
  that double as Ansible :children parents. A host-only net-mgmt network +
  config.ssh.insert_key=false make the transport static. mqlab vm inventory
  renders/echoes build/inventory.ini. Requires a guest rebuild for the mgmt
  NIC + shared key. vrg-validate green (100% branch).
```

- [ ] **Step 5: Push for finalize**

```bash
vrg-git push -u origin feature/101-static-inventory
```

Tell the human: ready for `vrg-submit-pr --finalize`.

---

## Self-Review

**Spec coverage:** §3 model → Tasks 4,7; §3.1 underscore convention → Tasks 4,7,9; §5 transport → Tasks 1,7; §6 renderer + command → Tasks 2,3,6; §6.3 retire inventory.sh → Task 10; §7 playbooks → Task 9; §8 mqlab internals → Tasks 4,5; §10 fail-loud → Task 3; §11 spike → Task 1; §12 testing → Tasks 2,3,8 + all; §13 setup rename → Tasks 4,7,9,10; §14 rebuild → Task 11. All sections covered.

**Type consistency:** `Setup.groups: list[str]` (Tasks 4,5,8); `render_inventory(topo: dict) -> str`, `InventoryError`, `lab_inventory()`, `inventory_path()` (Tasks 2,3,6,8) used consistently; `setup_members`/`setups_of` signatures unchanged (Tasks 4,5).

**Placeholder scan:** no TBD/TODO; every code step shows complete code; live-lab steps name exact commands + expected output.
