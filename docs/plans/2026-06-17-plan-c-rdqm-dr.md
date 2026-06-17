# Plan C — RDQM 3+3 cross-site HA/DR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a DR/HA RDQM (QMRDQM) across two 3-node sites and demonstrate a forced-DR cutover/failback with measurable RPO/RTO — RDQM DR parity with the Pacemaker arm.

**Architecture:** A new `site-rdqm-dr.yml` provisions both HA groups DR-ready (incl. a topology-rendered lab hosts file synced to the instrumented cluster). A standalone `rdqm-dr-qm-create.sh` creates the DR/HA QM with **two** `crtmqm` commands (one per site primary; each auto-creates that site's two HA secondaries) addressing DR partners by **net-wan IP**. DR ops are standalone scripts (`rdqm-dr-cutover.sh`, `rdqm-dr-force.sh`, `wan-degrade.sh`) mirroring the `pcmk-dr-*` family. Acceptance is a forced-DR drill → findings report.

**Tech Stack:** RHEL 9.6 x86_64 under TCG (functional only); IBM MQ 9.4.5 RDQM (DRBD+Pacemaker, driven via `crtmqm`/`rdqmadm`/`rdqmdr`/`rdqmint`); Ansible; vagrant-libvirt; `mqlab` (Typer/Python); `tc`/`netem`.

## Global Constraints

- Spec: `docs/specs/2026-06-17-plan-c-rdqm-dr-design.md` (authoritative).
- Validation is **only** `vrg-container-run -- vrg-validate` (ruff+mypy+ty+100% branch cov on `src/`+`tests/`, ansible syntax-check, shellcheck, markdownlint, audit). Never run individual linters; never mask a gate.
- Commit via `vrg-commit --type <t> --scope <s> --message <m> [--body <b>]`. Work only in this worktree on `feature/233-plan-c-rdqm-dr`.
- `src/` Python must hold **100% branch coverage**; tests seed a TOPO string + `MQLAB_REPO_ROOT` monkeypatch (see `tests/test_cli_qm.py`).
- DR replication is **async**; HA within a group is sync. DR partners addressed by **net-wan IPs**. **One floating IP per HA group** (#223): site-A `10.10.1.100`, site-B `10.10.2.100`.
- The forced-DR loss is a **hard power-off** of the site-A VMs — **never** `undefine`/erase; failback powers them back on.
- TCG = functional only; **no timing claims** in any report.
- net-wan IPs: rdqm-a1/2/3 = `10.99.0.31/.32/.33`; rdqm-b1/2/3 = `10.99.0.41/.42/.43`. DR port `7001`.

## File Structure

- `src/mqlab/hosts.py` (new) — render the canonical lab hosts file from topology (per-plane name aliases). Mirrors `src/mqlab/inventory.py`.
- `tests/test_hosts.py` (new) — unit tests, 100% branch cov.
- `src/mqlab/cli.py` (modify) — `vm hosts` command + render hosts alongside the inventory in `_provision`/`_create`.
- `ansible/site-rdqm-dr.yml` (new) — DR-ready substrate for both HA groups + hosts sync.
- `lab/scripts/rdqm-dr-qm-create.sh` (new) — DR/HA QM create (2 `crtmqm` + 2 `rdqmint` + MQSC).
- `lab/scripts/rdqm-dr-cutover.sh`, `rdqm-dr-force.sh`, `wan-degrade.sh` (new) — DR ops.
- `lab/topology.yaml` (modify) — point `rdqm_dr.provision` at `site-rdqm-dr.yml`.
- `docs/reports/2026-06-17-rdqm-forced-dr-findings.md` (new, Task 5) — acceptance findings.

---

### Task 1: Lab hosts renderer (`mqlab`)

**Files:**
- Create: `src/mqlab/hosts.py`
- Test: `tests/test_hosts.py`
- Modify: `src/mqlab/cli.py` (import + `vm hosts` command + render in `_provision` and `_create`)

**Interfaces:**
- Produces: `render_hosts(topo: dict) -> str`, `hosts_path() -> Path` (`build/hosts`), `lab_hosts() -> str`.
- Consumes: `mqlab.paths.repo_root` (as `inventory.py` does).

- [ ] **Step 1: Write the failing test** — `tests/test_hosts.py`:

```python
from __future__ import annotations

from mqlab.hosts import render_hosts

_TOPO = {
    "nodes": {
        "rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31", "net-data-a": "10.10.1.31",
                              "net-hb-a": "172.16.1.31", "net-wan": "10.99.0.31"}},
        "rdqm-b1": {"nics": {"net-mgmt": "10.50.0.41", "net-data-b": "10.10.2.41",
                             "net-wan": "10.99.0.41"}},
        "no-mgmt": {"nics": {"net-wan": "10.99.0.99"}},
        "bare": {},
    }
}


def test_bare_name_maps_to_mgmt_plus_mgmt_alias():
    out = render_hosts(_TOPO)
    assert "10.50.0.31 rdqm-a1 rdqm-a1-mgmt" in out


def test_per_plane_aliases_for_each_declared_nic():
    out = render_hosts(_TOPO)
    assert "10.10.1.31 rdqm-a1-data-a" in out
    assert "172.16.1.31 rdqm-a1-hb-a" in out
    assert "10.99.0.31 rdqm-a1-wan" in out
    assert "10.99.0.41 rdqm-b1-wan" in out


def test_node_without_mgmt_emits_only_plane_aliases():
    out = render_hosts(_TOPO)
    assert "no-mgmt-wan" in out  # plane alias present
    assert "\n10.99.0.99 no-mgmt\n" not in out  # no bare-name line without mgmt


def test_node_without_nics_emits_nothing_for_it():
    assert "bare" not in render_hosts(_TOPO)
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd <worktree> && uv run pytest tests/test_hosts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.hosts'`.

- [ ] **Step 3: Implement `src/mqlab/hosts.py`**

```python
"""Render a canonical /etc/hosts fragment from lab/topology.yaml (#233).

One name->IP map for the lab, with per-plane aliases so every node IP is addressable
by name: `<node>` and `<node>-mgmt` -> its net-mgmt IP; `<node>-<plane>` -> each other
NIC (plane = the NIC name minus the `net-` prefix). Mirrors inventory.py: a pure
function of topology, written under build/ and synced to the instrumented cluster.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


def render_hosts(topo: dict[str, Any]) -> str:
    """Project parsed topology -> /etc/hosts text (per-plane aliases)."""
    nodes = topo.get("nodes", {})
    lines: list[str] = []
    for host, spec in nodes.items():
        nics = (spec or {}).get("nics") or {}
        mgmt = nics.get("net-mgmt")
        if mgmt:
            lines.append(f"{mgmt} {host} {host}-mgmt")
        for nic, ip in nics.items():
            if nic == "net-mgmt":
                continue
            plane = nic.removeprefix("net-")
            lines.append(f"{ip} {host}-{plane}")
    return "\n".join(lines) + "\n"


def hosts_path() -> Path:
    """Where the rendered hosts file is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "hosts"


def lab_hosts() -> str:
    """Render the real lab/topology.yaml to /etc/hosts text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_hosts(topo)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_hosts.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Wire into the CLI** — in `src/mqlab/cli.py`, mirror the inventory wiring. Add to the inventory import line (top, ~line 20): `from mqlab.hosts import hosts_path, lab_hosts`. In `_provision` and `_create`, immediately after the existing `inv.write_text(lab_inventory())`, add:

```python
        hp = hosts_path()
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(lab_hosts())
        deps.renderer.note(f"rendered {hp}")
```

Add a `vm hosts` command next to `vm_inventory` (~line 716):

```python
@vm_app.command("hosts")
def vm_hosts() -> None:
    """Render build/hosts from topology and echo it (per-plane name aliases)."""
    deps = build_deps("vm-hosts", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_hosts()
        hp = hosts_path()
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(text)
        deps.renderer.note(f"rendered {hp}")
        deps.renderer.echo(text)
    finally:
        deps.transcript.close()
```

(Match the exact echo/transcript idiom of the existing `vm_inventory` body — copy its shape; the helper names above are illustrative of that function's pattern.)

- [ ] **Step 6: Add CLI tests** in `tests/test_cli_qm.py` (or a new `tests/test_cli_hosts.py`) asserting `vm hosts` renders `build/hosts` and that `_provision` writes it. Reuse the `_seed`/`RecordingRunner` harness already in `tests/test_cli_qm.py`. Assert `(tmp_path / "build" / "hosts").exists()` after `mqlab vm hosts`.

- [ ] **Step 7: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → green (100% cov includes the new branches: mgmt-present, mgmt-absent, no-nics).

```bash
vrg-commit --type feat --scope mqlab --message "render canonical lab hosts (per-plane name aliases) from topology"
```

---

### Task 2: DR-ready substrate — `ansible/site-rdqm-dr.yml` + topology wiring

**Files:**
- Create: `ansible/site-rdqm-dr.yml`
- Modify: `lab/topology.yaml` (`rdqm_dr.provision: ansible/site-rdqm-dr.yml`)

**Interfaces:**
- Consumes: `rdqm-install`, `rdqm-ha`, `mqweb` roles (Plan B); `build/hosts` (Task 1).
- Produces: two formed HA groups (rdqm_a, rdqm_b), MQ + REST installed, firewall open, hosts synced — ready for the create (Task 3).

- [ ] **Step 1: Write `ansible/site-rdqm-dr.yml`**

```yaml
# Plan C (#233): DR-ready substrate for the RDQM 3+3 cross-site HA/DR cluster.
# Provisions BOTH HA groups DR-ready from the start (RDQM cannot add DR to an
# existing HA QM). Mirrors site-pcmk-dr.yml's shape; reuses the Plan B roles.
- name: wait for all RDQM DR-arm hosts to be SSH-ready (cold-boot guard, #151/#160)
  hosts: rdqm_a:rdqm_b
  gather_facts: false
  tasks:
    - name: wait for connection
      ansible.builtin.wait_for_connection: { timeout: 180 }

- hosts: rdqm_a:rdqm_b
  become: true
  tasks:
    - name: acl package for unprivileged become
      ansible.builtin.dnf: { name: acl }
    # Canonical lab name resolution on the instrumented cluster: sync the
    # topology-rendered hosts file (build/hosts, mqlab) so every node IP is
    # addressable by name across both sites (per-plane aliases). #233.
    - name: sync the canonical lab hosts file
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../build/hosts"
        dest: /etc/hosts.lab
        mode: "0644"
    - name: include the lab hosts file from /etc/hosts (idempotent)
      ansible.builtin.lineinfile:
        path: /etc/hosts
        line: ".include /etc/hosts.lab"  # see Step 1a if .include unsupported
        state: present

- hosts: rdqm_a:rdqm_b
  roles: [rdqm-install]

- hosts: rdqm_a:rdqm_b
  become: true
  tasks:
    - name: open RDQM firewall ports (DRBD 7000-7100, MQ 1414) via IBM's services
      ansible.builtin.shell: |
        set -e
        firewall-cmd --permanent --add-service=rdqm-drbd
        firewall-cmd --permanent --add-service=rdqm-mq
        firewall-cmd --reload
      args: { creates: /etc/firewalld/.rdqm-dr-opened }
      register: fw
    - name: mark firewall opened
      ansible.builtin.file: { path: /etc/firewalld/.rdqm-dr-opened, state: touch }
      when: fw is changed

- hosts: rdqm_a:rdqm_b
  roles: [mqweb]   # REST on every QM (design §1); starts --no-block (Plan B TCG fix)

- hosts: rdqm_a
  vars:
    rdqm_site_nodes:
      - { name: rdqm-a1, hb: 172.16.1.31, data: 10.10.1.31 }
      - { name: rdqm-a2, hb: 172.16.1.32, data: 10.10.1.32 }
      - { name: rdqm-a3, hb: 172.16.1.33, data: 10.10.1.33 }
  roles: [rdqm-ha]

- hosts: rdqm_b
  vars:
    rdqm_site_nodes:
      - { name: rdqm-b1, hb: 172.16.2.41, data: 10.10.2.41 }
      - { name: rdqm-b2, hb: 172.16.2.42, data: 10.10.2.42 }
      - { name: rdqm-b3, hb: 172.16.2.43, data: 10.10.2.43 }
  roles: [rdqm-ha]
```

- [ ] **Step 1a (verify the hosts-include mechanism):** glibc `/etc/hosts` does **not** support `.include`. So instead of the `lineinfile .include`, append the lab entries into `/etc/hosts` with a marked block. Replace the two hosts tasks with:

```yaml
    - name: lab name resolution (per-plane aliases) in /etc/hosts
      ansible.builtin.blockinfile:
        path: /etc/hosts
        marker: "# {mark} LAB HOSTS (#233)"
        block: "{{ lookup('file', playbook_dir + '/../build/hosts') }}"
```

Use this `blockinfile` form (drop the `copy`+`lineinfile` pair). It is idempotent and re-syncs on change.

- [ ] **Step 2: Wire the setup** — in `lab/topology.yaml`, change `rdqm_dr.provision`:

```yaml
  rdqm_dr:
    description: RDQM 3+3 — site-A HA plus cross-site DR (rdqmdr async)
    arm: rdqm-rhel
    groups: [rdqm_a, rdqm_b]
    provision: ansible/site-rdqm-dr.yml
```

- [ ] **Step 3: Validate + commit**

Run: `vrg-container-run -- vrg-validate` (ansible syntax-check parses the new playbook; topology integrity tests pass).

```bash
vrg-commit --type feat --scope rdqm --message "site-rdqm-dr.yml: DR-ready 3+3 substrate + lab hosts sync"
```

---

### Task 3: DR/HA QM create — `lab/scripts/rdqm-dr-qm-create.sh`

**Files:**
- Create: `lab/scripts/rdqm-dr-qm-create.sh`

**Interfaces:**
- Consumes: the formed HA groups (Task 2); run after `mqlab vm provision distributed... rdqm_dr`.
- Produces: QMRDQM as a DR/HA RDQM (site A = HA+DR primary), two per-site FIPs, lab MQSC.

Canonical (worked example): **one `crtmqm` per site primary** auto-creates that site's
two HA secondaries. Site A is `-sx -rr p` (HA primary / DR primary); site B is
`-sx -rr s` (HA primary / DR secondary). DR partners by net-wan IP.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# lab/scripts/rdqm-dr-qm-create.sh - create the DR/HA RDQM QMRDQM across the 3+3
# cluster (#233). Standalone (not a qm-create registry verb): the registry resolves
# verbs per-arm and rdqm_ha/rdqm_dr share rdqm-rhel, so DR lives in the rdqm-dr-*
# family like the Pacemaker arm's pcmk-dr-*.sh. Canonical: IBM 9.4 "Creating DR/HA
# RDQMs" + worked example (one crtmqm per site primary fans out to its HA secondaries;
# DR partner by net-wan IP; DR replication on port 7001).
set -euo pipefail
QM="${1:-QMRDQM}"
VIP_A="${2:-10.10.1.100}"     # site-A floating IP (net-data-a)
VIP_B="${3:-10.10.2.100}"     # site-B floating IP (net-data-b)
A_WAN="10.99.0.31,10.99.0.32,10.99.0.33"   # site-A DR interfaces (net-wan)
B_WAN="10.99.0.41,10.99.0.42,10.99.0.43"   # site-B DR interfaces (net-wan)
DRPORT=7001
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

# Site A: HA primary + DR primary on rdqm-a1; fans out to rdqm-a2/a3 as HA secondaries.
run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -rr p -rl $A_WAN -ri $B_WAN -rp $DRPORT -fs 3072M $QM \
  || /opt/mqm/bin/dspmq -m $QM"
# Site B: HA primary + DR secondary on rdqm-b1; fans out to rdqm-b2/b3.
run rdqm-b1 "/opt/mqm/bin/crtmqm -sx -rr s -rl $B_WAN -ri $A_WAN -rp $DRPORT -fs 3072M $QM \
  || /opt/mqm/bin/dspmq -m $QM"

# Floating IP per HA group (one per group, #223) on each site's primary node.
IFACE_A=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP_A%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_A -l $IFACE_A || true"
IFACE_B=$(ansible rdqm-b1 -m shell -a "ip -br addr | grep ${VIP_B%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-b1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_B -l $IFACE_B || true"

# Lab MQSC posture (same as Plan B). Defined on the active DR primary (site A); the
# QM's object data replicates to site B via DR.
run rdqm-a1 "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
```

- [ ] **Step 2: `chmod +x` + validate + commit**

```bash
chmod +x lab/scripts/rdqm-dr-qm-create.sh
vrg-container-run -- vrg-validate   # shellcheck scope note: lab/scripts may be outside it; keep clean regardless
vrg-commit --type feat --scope rdqm --message "rdqm-dr-qm-create.sh: DR/HA QMRDQM create (2 crtmqm + 2 FIPs)"
```

---

### Task 4: DR operations — cutover, forced, wan-degrade

**Files:**
- Create: `lab/scripts/rdqm-dr-cutover.sh`, `lab/scripts/rdqm-dr-force.sh`, `lab/scripts/wan-degrade.sh`

**Interfaces:** consume the running DR/HA QMRDQM (Task 3); used by the drill (Task 5).

- [ ] **Step 1: `rdqm-dr-cutover.sh` (controlled)**

```bash
#!/usr/bin/env bash
# Controlled cross-site cutover/failback of the DR/HA RDQM (#233). rdqmdr -s demotes
# the current DR primary; rdqmdr -p promotes the peer. Canonical: IBM 9.4 rdqmdr
# (the -p promote fails if the old primary is still running with the link up — the
# built-in split-brain guard, so we demote the source first).
# Usage: rdqm-dr-cutover.sh a2b|b2a [QM=QMRDQM]
set -euo pipefail
DIR="${1:?usage: rdqm-dr-cutover.sh a2b|b2a [QM]}"; QM="${2:-QMRDQM}"
case "$DIR" in
  a2b) FROM=rdqm-a1; TO=rdqm-b1 ;;
  b2a) FROM=rdqm-b1; TO=rdqm-a1 ;;
  *) echo "direction must be a2b|b2a" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
run "$FROM" "/opt/mqm/bin/rdqmdr -m $QM -s"   # demote source to DR secondary
run "$TO"   "/opt/mqm/bin/rdqmdr -m $QM -p"   # promote peer to DR primary
run "$TO"   "/opt/mqm/bin/rdqmstatus -m $QM"
echo "=== cutover $DIR complete; DR primary is now on $TO ==="
```

- [ ] **Step 2: `rdqm-dr-force.sh` (forced — site A hard-powered-off)**

```bash
#!/usr/bin/env bash
# FORCED cross-site cutover (#233): site A is GONE (hard power-off), so we cannot and
# need not demote it — RDQM's guard passes because the link is down. We promote site B.
# The caller powers site A off first (lab: `virsh destroy lab_rdqm-a{1,2,3}` — libvirt
# force-OFF, the domains/disks PERSIST; never undefine). Failback = power A on, then
# rdqm-dr-cutover.sh b2a.
# Usage: rdqm-dr-force.sh [QM=QMRDQM] [TO=rdqm-b1]
set -euo pipefail
QM="${1:-QMRDQM}"; TO="${2:-rdqm-b1}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
run "$TO" "/opt/mqm/bin/rdqmdr -m $QM -p"        # force-promote the recovery site
run "$TO" "su mqm -c '/opt/mqm/bin/strmqm $QM'"  # start the QM on the new primary
# Fail loud: complete only if the QM is actually Running on the recovery site.
if run "$TO" "/opt/mqm/bin/dspmq -m $QM -o status" | grep -q "STATUS(Running)"; then
  echo "=== forced cutover complete; QMRDQM Running on $TO ==="
else
  echo "ERROR: forced cutover did NOT bring QMRDQM up on $TO" >&2; exit 1
fi
```

- [ ] **Step 3: `wan-degrade.sh` (netem on net-wan, RPO window)**

```bash
#!/usr/bin/env bash
# Widen the cross-site DR async window so RPO is measurable (#233). RDQM hides DRBD,
# so we degrade the only layer it leaves open — the net-wan replication link — with
# tc/netem on the ACTIVE site-A primary's net-wan egress. Intra-site HA (net-hb/
# net-data) is untouched. Usage: wan-degrade.sh on|off [NODE=rdqm-a1] [DELAY=80ms] [LOSS=5%]
set -euo pipefail
ACTION="${1:?usage: wan-degrade.sh on|off [node] [delay] [loss]}"
NODE="${2:-rdqm-a1}"; DELAY="${3:-80ms}"; LOSS="${4:-5%}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
IFACE=$(ansible "$NODE" -m shell -a "ip -br addr | grep 10.99.0. | cut -d' ' -f1" | tail -1)
case "$ACTION" in
  on)  run "$NODE" "tc qdisc replace dev $IFACE root netem delay $DELAY loss $LOSS" ;;
  off) run "$NODE" "tc qdisc del dev $IFACE root || true" ;;
  *) echo "action must be on|off" >&2; exit 2 ;;
esac
echo "=== wan-degrade $ACTION on $NODE:$IFACE (delay=$DELAY loss=$LOSS) ==="
```

- [ ] **Step 4: `chmod +x` all three + validate + commit**

```bash
chmod +x lab/scripts/rdqm-dr-cutover.sh lab/scripts/rdqm-dr-force.sh lab/scripts/wan-degrade.sh
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope rdqm --message "rdqm DR ops: cutover, forced cutover, wan-degrade (netem)"
```

---

### Task 5: Forced-DR drill + findings report (acceptance)

**Files:**
- Create: `docs/reports/2026-06-17-rdqm-forced-dr-findings.md`

This task runs the live lab. Drive it from the worktree (worktree-lab dance: real `build/`, symlinked sub-artifacts, copied `lab/.vagrant`).

- [ ] **Step 1: Bring up the DR arm + create the QM**

```bash
uv run mqlab vm up rdqm-a ; uv run mqlab vm up rdqm-b ; uv run mqlab vm up app-client
uv run mqlab vm provision rdqm_dr          # renders build/hosts + runs site-rdqm-dr.yml
bash lab/scripts/rdqm-dr-qm-create.sh      # 2 crtmqm + 2 FIPs + MQSC
uv run mqlab qm status rdqm_dr             # rdqmstatus: site A HA Normal, DR primary
```

Expected: `rdqmstatus` shows site A HA `Normal`, DR role primary; site B DR secondary.

- [ ] **Step 2: Snapshot the provisioned 3+3 (#218)** so the drill can be re-run cheaply:

```bash
bash lab/scripts/lab-snapshot.sh distributed-rdqm-dr lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3 lab_rdqm-b1 lab_rdqm-b2 lab_rdqm-b3 lab_app-client
```

- [ ] **Step 3: Baseline + degrade + load**

```bash
bash lab/scripts/e2e-test.sh 5 QMRDQM '10.10.1.100(1414),10.10.2.100(1414)'   # baseline round-trips
bash lab/scripts/wan-degrade.sh on rdqm-a1 80ms 5%
# drive sustained load (records sent-and-acked ids for RPO accounting):
uv run mqlab vm ssh app-client -- "~/mqvenv/bin/python ~/app_requester.py --qm QMRDQM --conn '10.10.1.100(1414),10.10.2.100(1414)' --count 200 --interval 0.2" &
```

- [ ] **Step 4: Force the disaster (hard power-off — NOT erase) + promote site B**

```bash
for n in 1 2 3; do virsh -c qemu:///system destroy lab_rdqm-a$n; done   # force power-OFF; domains persist
bash lab/scripts/rdqm-dr-force.sh QMRDQM rdqm-b1
```

Expected: `rdqm-dr-force.sh` prints "QMRDQM Running on rdqm-b1". The app (reconnect-enabled) resumes against `10.10.2.100`.

- [ ] **Step 5: Measure RPO/RTO, then fail back**

Compute **RPO** = (ids the app logged as sent-and-acked at A) − (ids that survived on site B, via `runmqsc DISPLAY QSTATUS(HA.TEST) CURDEPTH` / message ids). **RTO** = the app's reconnect-and-resume gap. Then:

```bash
for n in 1 2 3; do virsh -c qemu:///system start lab_rdqm-a$n; done   # power site A back ON
# wait for RDQM to resync site A as DR-secondary (poll rdqmstatus), then:
bash lab/scripts/wan-degrade.sh off rdqm-a1
bash lab/scripts/rdqm-dr-cutover.sh b2a QMRDQM
uv run mqlab qm status rdqm_dr   # back to site-A DR primary, HA Normal
```

- [ ] **Step 6: Write the findings report** — `docs/reports/2026-06-17-rdqm-forced-dr-findings.md`, mirroring `2026-06-09-forced-dr-findings.md`: setup, the symptom→cause→fix log, **RPO/RTO observed** (data vs judgment), the single-FIP-per-group note (#223), and the encapsulation-trade-off observation (netem vs `drbd-degrade.sh`). TCG = functional only; **no timing claims**.

- [ ] **Step 7: Validate + commit**

```bash
vrg-container-run -- vrg-validate   # markdownlint the report
vrg-commit --type docs --scope rdqm --message "RDQM forced-DR drill findings (3+3 cutover/failback, RPO/RTO)"
```

---

## Self-Review

- **Spec coverage:** §1 scope → Task 5 acceptance; §2 from-scratch → Task 3 (`crtmqm` fresh); §3 addressing/FIPs → Tasks 1/3; §4 substrate (hosts, firewall, mqweb, HA groups) → Tasks 1/2; §5 create → Task 3; §6 app connectivity → Task 5 Step 3 (CONNAME list); §7 DR ops + drill → Tasks 4/5; §8 wiring → Task 2 + Task 1. All covered.
- **Placeholders:** none — all `crtmqm`/`rdqmdr`/`rdqmint`/`tc` commands and the renderer code are concrete and canonical.
- **Type consistency:** `render_hosts`/`hosts_path`/`lab_hosts` names match across Task 1 code, tests, and CLI wiring.
- **Open detail for the implementer:** confirm `shellcheck`'s scope in `vrg-validate` covers `lab/scripts/` (Plan B's scripts suggested it may not); keep the scripts clean regardless. The `mqlab vm ssh`/`vm up` exact subcommand spellings should be confirmed against `cli.py` at execution (Plan B used `uv run mqlab vm ...`).
