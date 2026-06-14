# Cluster Drill Cockpit — Plan 1a: Cluster-State Collector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the headless telemetry half of the PCMK cluster drill cockpit — a stdlib-only Python collector that turns live `crm_mon` / `drbdadm` / `stonith_admin` / `iscsiadm` / `multipath` output into `node_exporter` textfile metrics on the pcmk and san nodes, with fail-loud per-source staleness and bounded, non-blocking probes.

**Architecture:** A single self-contained module `src/mqlab/clusterstate.py` holds pure parse functions (command-output text → metric dicts) + a render function (dicts → Prometheus textfile) + a bounded probe runner + a `main()`. It is unit-tested in the repo against fixtures captured from the live lab, *and* deployed verbatim to the guest nodes as `/usr/local/bin/lab-cluster-state` (run by a 5s systemd timer, like the existing `net-reach` collector). No new Prometheus job — the metrics ride the existing `node` scrape job's textfile collector. This plan produces no Grafana changes; it is verifiable entirely with `curl`/`promtool` against the node's `/metrics`.

**Tech Stack:** Python 3 (stdlib only — `subprocess`, `xml.etree.ElementTree`, `json`), pytest (`uv run pytest`, 100% branch coverage gate), Ansible (collector role: copy module + systemd service + timer), systemd timers, `node_exporter` textfile collector.

**Spec:** `docs/specs/2026-06-14-cluster-drill-cockpit-design.md` (§4 telemetry, §4.2 cadence/bounding, §4.3 fail-loud + source precedence).

---

## Conventions for this plan

- **Git:** this repo forbids raw `git`. Use `vrg-git add …` and
  `vrg-commit --type <type> --scope obs --message "<msg>"`. Work happens in the
  worktree `.worktrees/issue-177-cluster-cockpit` on branch
  `feature/177-cluster-cockpit`.
- **Inner-loop tests:** `uv run pytest <path> -v` (per repo validation-gotchas:
  always `uv run`, never bare `pytest`).
- **Validation gate (run before each commit that touches code):**
  `vrg-container-run -- vrg-validate`. This is the *only* validation command;
  it enforces ruff (incl. magic-trailing-comma), typing, and **100% branch
  coverage**. Do not run individual linters.
- **Coverage:** every function and every branch needs a test. Untested branches
  fail `vrg-validate`. Write the test in the same task that adds the branch.

## File structure

| File | Responsibility |
|---|---|
| `tests/fixtures/clusterstate/*.xml`, `*.json`, `*.txt` | Real command output captured from the live lab (Task 1); the parsers' test inputs. |
| `src/mqlab/clusterstate.py` | **New.** Stdlib-only: parse functions, `render_cluster_state_prom`, bounded `probe()` runner, `main()`. Importable for tests; runnable standalone on a guest. |
| `tests/test_clusterstate.py` | **New.** Unit tests for every parse/render/runner branch, fed by the fixtures. |
| `ansible/roles/cluster-state/tasks/main.yml` | **New.** Deploy the module as `/usr/local/bin/lab-cluster-state`, install service + timer, enable. |
| `ansible/roles/cluster-state/templates/lab-cluster-state.service.j2` | **New.** `oneshot` unit running the collector for this node's role. |
| `ansible/roles/cluster-state/templates/lab-cluster-state.timer.j2` | **New.** 5s timer. |
| `ansible/site-pcmk.yml` (or the cluster bring-up playbook) | **Modify.** Apply `cluster-state` to the `pcmk_*` and `san_*` groups with the right `role` var. |

**Node-role split:** pcmk nodes run the `cluster` probe set (crm_mon, stonith, iscsi, daemons); san nodes run the `storage` probe set (drbdadm, daemons). The deployed script takes a `--role {cluster,storage}` argument so one module serves both.

---

### Task 1: Capture real command output into fixtures *(spike — needs the lab up)*

**Files:**
- Create: `tests/fixtures/clusterstate/crm_mon.xml`
- Create: `tests/fixtures/clusterstate/drbd_status.json`
- Create: `tests/fixtures/clusterstate/stonith_history.txt`
- Create: `tests/fixtures/clusterstate/iscsi_session.txt`
- Create: `tests/fixtures/clusterstate/multipath.txt`

The parsers in later tasks are written against these. The lab must be up
(`distributed`/`pcmk_san_ha` setup). If the lab is not currently up, ask the human
to bring it up — this task cannot be faked, and per the repo's spike-first culture the
parsers must match *real* output, not assumed schemas.

- [ ] **Step 1: Capture from a pcmk node**

The human (or an authorized session) runs, on `pcmk-a1`:

```bash
sudo crm_mon --one-shot --output-as=xml            # -> crm_mon.xml
sudo stonith_admin --history '*' 2>/dev/null || true   # -> stonith_history.txt
sudo iscsiadm -m session 2>/dev/null || true       # -> iscsi_session.txt
sudo multipath -ll 2>/dev/null || true             # -> multipath.txt
```

- [ ] **Step 2: Capture from a san node**

On `san-a`:

```bash
sudo drbdadm status --json 2>/dev/null || sudo drbdsetup status --json   # -> drbd_status.json
```

- [ ] **Step 3: Save the captured text** into the five fixture files under
  `tests/fixtures/clusterstate/`, verbatim. If a command differs from the spec's
  assumption (e.g. `--output-as=xml` vs deprecated `--as-xml`, or `drbdadm` lacking
  `--json` so `drbdsetup status --json` is the source), **record the working command
  in a comment at the top of the fixture** — later tasks' `main()` must invoke exactly
  that command.

- [ ] **Step 4: Commit the fixtures**

```bash
vrg-git add tests/fixtures/clusterstate/
vrg-commit --type test --scope obs --message "capture live cluster-state fixtures (#177)"
```

> If you cannot bring the lab up right now, you may proceed using the representative
> samples embedded in Tasks 3–5 as the fixture contents, but you MUST re-run this task
> against the live lab and reconcile before Plan 1a is considered done — the §11
> success criteria require a real cold-rebuild drill.

---

### Task 2: Module scaffold + standalone-runnable shape

**Files:**
- Create: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clusterstate.py
from __future__ import annotations

from mqlab import clusterstate


def test_module_exposes_role_probe_sets():
    # cluster nodes probe crm/stonith/iscsi/daemons; san nodes probe drbd/daemons
    assert clusterstate.PROBE_SETS["cluster"] == ("crm", "stonith", "iscsi", "daemons")
    assert clusterstate.PROBE_SETS["storage"] == ("drbd", "daemons")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clusterstate.py::test_module_exposes_role_probe_sets -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.clusterstate'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/clusterstate.py
"""Cluster-state collector for the PCMK drill cockpit (#177, Plan 1a).

Stdlib-only so this exact file is deployed verbatim to the pcmk/san guests as
/usr/local/bin/lab-cluster-state and run by a 5s systemd timer, AND imported by the
repo's unit tests. Pure parse functions turn command output into metric rows;
render_cluster_state_prom turns rows into a node_exporter textfile; probe() runs each
source bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).
"""

from __future__ import annotations

# role -> ordered probe sources it runs
PROBE_SETS: dict[str, tuple[str, ...]] = {
    "cluster": ("crm", "stonith", "iscsi", "daemons"),
    "storage": ("drbd", "daemons"),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_clusterstate.py::test_module_exposes_role_probe_sets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "cluster-state collector module scaffold (#177)"
```

---

### Task 3: Parse `crm_mon --output-as=xml` (quorum, nodes, resource placement)

**Files:**
- Modify: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

Representative fixture shape (Task 1 confirms exact attributes; `crm_mon` XML is a
stable documented schema):

```xml
<pacemaker-result api-version="2.x" request="crm_mon --output-as=xml">
  <summary>
    <current_dc present="true" name="pcmk-a1" id="1" with_quorum="true"/>
    <nodes_configured number="3"/>
    <resources_configured number="4" disabled="0" blocked="0"/>
  </summary>
  <nodes>
    <node name="pcmk-a1" online="true" standby="false" unclean="false"/>
    <node name="pcmk-a2" online="true" standby="false" unclean="false"/>
    <node name="pcmk-a3" online="true" standby="false" unclean="false"/>
  </nodes>
  <resources>
    <group id="mq_group">
      <resource id="mq_fs"      role="Started" active="true"><node name="pcmk-a2"/></resource>
      <resource id="mq_vip"     role="Started" active="true"><node name="pcmk-a2"/></resource>
      <resource id="mq_vip_ext" role="Started" active="true"><node name="pcmk-a2"/></resource>
      <resource id="mq_qm"      role="Started" active="true"><node name="pcmk-a2"/></resource>
    </group>
  </resources>
</pacemaker-result>
```

- [ ] **Step 1: Write the failing test**

```python
def test_parse_crm_extracts_quorum_nodes_and_resource_placement():
    xml = (FIXTURES / "crm_mon.xml").read_text()
    out = clusterstate.parse_crm(xml)
    assert out["quorate"] is True
    assert out["nodes"]["pcmk-a2"] == {"online": True, "standby": False, "unclean": False}
    # resource -> (state, holding node or None)
    assert out["resources"]["mq_qm"] == {"state": "Started", "node": "pcmk-a2"}
    assert out["resources"]["mq_fs"]["node"] == "pcmk-a2"


def test_parse_crm_marks_offline_node_and_unplaced_resource():
    xml = """<pacemaker-result>
      <summary><current_dc with_quorum="false"/></summary>
      <nodes><node name="pcmk-a2" online="false" standby="false" unclean="true"/></nodes>
      <resources><group id="mq_group">
        <resource id="mq_qm" role="Stopped" active="false"/>
      </group></resources>
    </pacemaker-result>"""
    out = clusterstate.parse_crm(xml)
    assert out["quorate"] is False
    assert out["nodes"]["pcmk-a2"]["unclean"] is True
    assert out["resources"]["mq_qm"] == {"state": "Stopped", "node": None}
```

Add the fixtures path constant at the top of the test file:

```python
from pathlib import Path
FIXTURES = Path(__file__).parent / "fixtures" / "clusterstate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clusterstate.py -k parse_crm -v`
Expected: FAIL — `AttributeError: module 'mqlab.clusterstate' has no attribute 'parse_crm'`

- [ ] **Step 3: Write minimal implementation** (append to `clusterstate.py`)

```python
import xml.etree.ElementTree as ET


def parse_crm(xml_text: str) -> dict:
    """crm_mon --output-as=xml -> {quorate, nodes{name:{online,standby,unclean}}, resources{id:{state,node}}}."""
    root = ET.fromstring(xml_text)
    dc = root.find("./summary/current_dc")
    quorate = dc is not None and dc.get("with_quorum") == "true"

    nodes: dict[str, dict[str, bool]] = {}
    for n in root.findall("./nodes/node"):
        nodes[n.get("name", "")] = {
            "online": n.get("online") == "true",
            "standby": n.get("standby") == "true",
            "unclean": n.get("unclean") == "true",
        }

    resources: dict[str, dict] = {}
    for r in root.findall(".//resources//resource"):
        rid = r.get("id", "")
        held = r.find("./node")
        resources[rid] = {
            "state": r.get("role", "Unknown"),
            "node": held.get("name") if held is not None else None,
        }
    return {"quorate": quorate, "nodes": nodes, "resources": resources}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_clusterstate.py -k parse_crm -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "parse crm_mon quorum/nodes/resources (#177)"
```

---

### Task 4: Parse `drbdadm status --json` (role, disk, connection, resync %, out-of-sync)

**Files:**
- Modify: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

Representative fixture shape (`drbdsetup status --json` schema is an array of resources):

```json
[
  {
    "name": "r0",
    "role": "Primary",
    "devices": [{"volume": 0, "disk-state": "UpToDate"}],
    "connections": [
      {"peer-node-id": 1, "name": "san-b", "connection-state": "Connected",
       "peer-role": "Secondary",
       "peer_devices": [{"volume": 0, "peer-disk-state": "UpToDate",
                          "replication-state": "Established",
                          "percent-in-sync": 100.0, "out-of-sync": 0}]}
    ]
  }
]
```

- [ ] **Step 1: Write the failing test**

```python
def test_parse_drbd_extracts_role_disk_conn_and_rpo_tail():
    out = clusterstate.parse_drbd((FIXTURES / "drbd_status.json").read_text())
    r0 = out["r0"]
    assert r0["role"] == "Primary"
    assert r0["disk"] == "UpToDate"
    assert r0["conn"] == "Connected"
    assert r0["resync_pct"] == 100.0
    assert r0["out_of_sync_bytes"] == 0


def test_parse_drbd_flags_split_brain_standalone_and_resync_tail():
    text = """[{"name":"r0","role":"Secondary",
      "devices":[{"volume":0,"disk-state":"Outdated"}],
      "connections":[{"name":"san-b","connection-state":"StandAlone","peer-role":"Unknown",
        "peer_devices":[{"volume":0,"peer-disk-state":"DUnknown",
          "replication-state":"Off","percent-in-sync":42.0,"out-of-sync":2202010}]}]}]"""
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "StandAlone"      # split-brain / disconnected
    assert r0["disk"] == "Outdated"
    assert r0["resync_pct"] == 42.0
    assert r0["out_of_sync_bytes"] == 2202010


def test_parse_drbd_handles_no_connections():
    text = '[{"name":"r0","role":"Secondary","devices":[{"volume":0,"disk-state":"Diskless"}],"connections":[]}]'
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "Disconnected"
    assert r0["disk"] == "Diskless"
    assert r0["resync_pct"] is None
    assert r0["out_of_sync_bytes"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clusterstate.py -k parse_drbd -v`
Expected: FAIL — no attribute `parse_drbd`

- [ ] **Step 3: Write minimal implementation** (append)

```python
import json


def parse_drbd(json_text: str) -> dict:
    """drbdsetup/drbdadm status --json -> {resource: {role, disk, conn, resync_pct, out_of_sync_bytes}}."""
    out: dict[str, dict] = {}
    for res in json.loads(json_text):
        dev0 = (res.get("devices") or [{}])[0]
        conns = res.get("connections") or []
        if conns:
            conn0 = conns[0]
            peerdev0 = (conn0.get("peer_devices") or [{}])[0]
            conn = conn0.get("connection-state", "Unknown")
            resync = peerdev0.get("percent-in-sync")
            oos = peerdev0.get("out-of-sync")
        else:
            conn, resync, oos = "Disconnected", None, None
        out[res.get("name", "")] = {
            "role": res.get("role", "Unknown"),
            "disk": dev0.get("disk-state", "Unknown"),
            "conn": conn,
            "resync_pct": resync,
            "out_of_sync_bytes": oos,
        }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_clusterstate.py -k parse_drbd -v`
Expected: PASS (three tests)

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "parse drbd role/disk/conn/resync/RPO tail (#177)"
```

---

### Task 5: Parse stonith history, iSCSI sessions, multipath, and daemon liveness

**Files:**
- Modify: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_stonith_counts_recent_fence_actions_per_node():
    text = (
        "pcmk-a2 was reset (off) by pcmk-a1 at Sat Jun 14 12:04:01 2026\n"
        "pcmk-a2 was reset (on) by pcmk-a1 at Sat Jun 14 12:05:10 2026\n"
    )
    # node -> count of fence actions seen in history (0 == clean)
    assert clusterstate.parse_stonith(text) == {"pcmk-a2": 2}


def test_parse_stonith_empty_history_is_clean():
    assert clusterstate.parse_stonith("") == {}


def test_parse_iscsi_paths_counts_sessions():
    text = "tcp: [1] 10.40.1.5:3260,1 iqn.2003-01.lab:san-a (non-flash)\n"
    assert clusterstate.parse_iscsi(text) == 1


def test_parse_iscsi_no_sessions_is_zero():
    assert clusterstate.parse_iscsi("iscsiadm: No active sessions.\n") == 0


def test_parse_daemons_reads_systemctl_is_active_block():
    # one "is-active" line per unit, in PROBE order
    text = "active\nactive\nfailed\n"
    units = ["corosync", "pacemaker", "drbd"]
    assert clusterstate.parse_daemons(text, units) == {
        "corosync": True, "pacemaker": True, "drbd": False,
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_clusterstate.py -k "parse_stonith or parse_iscsi or parse_daemons" -v`
Expected: FAIL — missing attributes

- [ ] **Step 3: Write minimal implementation** (append)

```python
def parse_stonith(text: str) -> dict[str, int]:
    """stonith_admin --history '*' -> {node: fence_action_count}; empty == clean."""
    counts: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if " was reset " in line or " was fenced " in line:
            node = line.split(" ", 1)[0]
            counts[node] = counts.get(node, 0) + 1
    return counts


def parse_iscsi(text: str) -> int:
    """iscsiadm -m session -> count of active sessions (lines starting with a transport)."""
    return sum(1 for raw in text.splitlines() if raw.strip().startswith(("tcp:", "iser:")))


def parse_daemons(text: str, units: list[str]) -> dict[str, bool]:
    """One `systemctl is-active` line per unit (same order) -> {unit: is_active}."""
    lines = text.splitlines()
    return {unit: (lines[i].strip() == "active" if i < len(lines) else False)
            for i, unit in enumerate(units)}
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_clusterstate.py -k "parse_stonith or parse_iscsi or parse_daemons" -v`
Expected: PASS (five tests)

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "parse stonith/iscsi/daemon signals (#177)"
```

---

### Task 6: Render parsed rows to a Prometheus textfile, with per-source timestamps

**Files:**
- Modify: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

Metrics emitted (label `node` = the reporting node; cluster-wide facts are emitted by
every cluster node and the dashboard reads `max by(...)`):

- `cluster_quorate{node} 0|1`
- `cluster_node_online{node,member} 0|1`, `cluster_node_unclean{node,member} 0|1`
- `cluster_resource_started{node,resource} 0|1`, `cluster_resource_owner{node,resource,holder} 1`
- `cluster_drbd_role{node,resource,role} 1`, `cluster_drbd_disk{...}`, `cluster_drbd_conn{...}` (info-style: value 1 on the active enum label)
- `cluster_drbd_resync_pct{node,resource}`, `cluster_drbd_out_of_sync_bytes{node,resource}`
- `cluster_fence_count{node,member}`
- `cluster_iscsi_sessions{node}`
- `cluster_daemon_up{node,unit} 0|1`
- `cluster_state_last_write_timestamp{node,source}` — **per source**, so one stale source (§4.2 timeout) renders only its cells STALE.

- [ ] **Step 1: Write the failing test**

```python
def test_render_cluster_section_emits_quorum_resource_and_timestamp():
    crm = {
        "quorate": True,
        "nodes": {"pcmk-a2": {"online": True, "standby": False, "unclean": False}},
        "resources": {"mq_qm": {"state": "Started", "node": "pcmk-a2"}},
    }
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm, stonith={"pcmk-a2": 0}, iscsi=2,
        daemons={"corosync": True, "pacemaker": True},
        drbd=None, now=1781455000, fresh_sources=("crm", "stonith", "iscsi", "daemons"),
    )
    assert 'cluster_quorate{node="pcmk-a1"} 1' in out
    assert 'cluster_resource_started{node="pcmk-a1",resource="mq_qm"} 1' in out
    assert 'cluster_resource_owner{node="pcmk-a1",resource="mq_qm",holder="pcmk-a2"} 1' in out
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 2' in out
    assert 'cluster_daemon_up{node="pcmk-a1",unit="corosync"} 1' in out
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="crm"} 1781455000' in out


def test_render_omits_timestamp_for_stale_source():
    # crm timed out this cycle -> not in fresh_sources -> no crm timestamp (cells go STALE)
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a1", crm=None, stonith={}, iscsi=0, daemons={},
        drbd=None, now=1781455000, fresh_sources=("stonith", "iscsi", "daemons"),
    )
    assert 'source="crm"' not in out
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="stonith"} 1781455000' in out


def test_render_storage_section_emits_drbd_enums_and_rpo():
    drbd = {"r0": {"role": "Primary", "disk": "UpToDate", "conn": "StandAlone",
                   "resync_pct": 42.0, "out_of_sync_bytes": 2202010}}
    out = clusterstate.render_cluster_state_prom(
        node="san-a", crm=None, stonith=None, iscsi=None, daemons={"drbd": True},
        drbd=drbd, now=1781455000, fresh_sources=("drbd", "daemons"),
    )
    assert 'cluster_drbd_conn{node="san-a",resource="r0",conn="StandAlone"} 1' in out
    assert 'cluster_drbd_out_of_sync_bytes{node="san-a",resource="r0"} 2202010' in out
    assert 'cluster_drbd_resync_pct{node="san-a",resource="r0"} 42.0' in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_clusterstate.py -k render -v`
Expected: FAIL — no attribute `render_cluster_state_prom`

- [ ] **Step 3: Write minimal implementation** (append)

```python
def render_cluster_state_prom(
    *, node: str, crm: dict | None, stonith: dict | None, iscsi: int | None,
    daemons: dict, drbd: dict | None, now: int, fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed probe results -> node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if crm is not None:
        lines.append(f'cluster_quorate{{node="{node}"}} {1 if crm["quorate"] else 0}')
        for member, st in crm["nodes"].items():
            lines.append(f'cluster_node_online{{node="{node}",member="{member}"}} {1 if st["online"] else 0}')
            lines.append(f'cluster_node_unclean{{node="{node}",member="{member}"}} {1 if st["unclean"] else 0}')
        for rid, r in crm["resources"].items():
            lines.append(f'cluster_resource_started{{node="{node}",resource="{rid}"}} {1 if r["state"] == "Started" else 0}')
            if r["node"]:
                lines.append(f'cluster_resource_owner{{node="{node}",resource="{rid}",holder="{r["node"]}"}} 1')

    if stonith is not None:
        for member, count in stonith.items():
            lines.append(f'cluster_fence_count{{node="{node}",member="{member}"}} {count}')

    if iscsi is not None:
        lines.append(f'cluster_iscsi_sessions{{node="{node}"}} {iscsi}')

    for unit, up in daemons.items():
        lines.append(f'cluster_daemon_up{{node="{node}",unit="{unit}"}} {1 if up else 0}')

    if drbd is not None:
        for res, d in drbd.items():
            for kind in ("role", "disk", "conn"):
                lines.append(f'cluster_drbd_{kind}{{node="{node}",resource="{res}",{kind}="{d[kind]}"}} 1')
            if d["resync_pct"] is not None:
                lines.append(f'cluster_drbd_resync_pct{{node="{node}",resource="{res}"}} {d["resync_pct"]}')
            if d["out_of_sync_bytes"] is not None:
                lines.append(f'cluster_drbd_out_of_sync_bytes{{node="{node}",resource="{res}"}} {d["out_of_sync_bytes"]}')

    for source in fresh_sources:
        lines.append(f'cluster_state_last_write_timestamp{{node="{node}",source="{source}"}} {now}')

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_clusterstate.py -k render -v`
Expected: PASS (three tests)

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "render cluster-state textfile with per-source timestamps (#177)"
```

---

### Task 7: Bounded, non-blocking probe runner + `main()`

**Files:**
- Modify: `src/mqlab/clusterstate.py`
- Test: `tests/test_clusterstate.py`

`probe()` runs one command with a hard timeout; on success returns its stdout, on
timeout/error returns `None` (the caller drops that source from `fresh_sources` →
STALE). This is the §4.2 bounded/non-blocking requirement. `main()` assembles the
role's probe set, writes the textfile atomically (tmp→rename), and is invoked by the
systemd unit.

- [ ] **Step 1: Write the failing test**

```python
import subprocess


def test_probe_returns_stdout_on_success(monkeypatch):
    def fake_run(cmd, **kw):
        assert kw["timeout"] == 3
        return subprocess.CompletedProcess(cmd, 0, stdout="hello\n", stderr="")
    monkeypatch.setattr(clusterstate.subprocess, "run", fake_run)
    assert clusterstate.probe(["echo", "hi"], timeout=3) == "hello\n"


def test_probe_returns_none_on_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(clusterstate.subprocess, "run", fake_run)
    assert clusterstate.probe(["sleep", "9"], timeout=3) is None


def test_probe_returns_none_on_nonzero_or_oserror(monkeypatch):
    monkeypatch.setattr(clusterstate.subprocess, "run",
                        lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"))
    assert clusterstate.probe(["false"], timeout=3) is None
    monkeypatch.setattr(clusterstate.subprocess, "run",
                        lambda c, **k: (_ for _ in ()).throw(OSError("no such binary")))
    assert clusterstate.probe(["nope"], timeout=3) is None


def test_main_writes_textfile_atomically_for_storage_role(tmp_path, monkeypatch):
    drbd_json = (FIXTURES / "drbd_status.json").read_text()
    monkeypatch.setattr(clusterstate, "probe",
                        lambda cmd, timeout: drbd_json if "drbd" in cmd[0] else "active\n")
    out = tmp_path / "lab_cluster_state.prom"
    clusterstate.main(["--role", "storage", "--node", "san-a", "--out", str(out), "--now", "1781455000"])
    text = out.read_text()
    assert 'cluster_drbd_role{node="san-a",resource="r0",role="Primary"} 1' in text
    assert 'source="drbd"' in text
    assert not (tmp_path / "lab_cluster_state.prom.tmp").exists()  # atomic move cleaned up
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_clusterstate.py -k "probe or main" -v`
Expected: FAIL — no attribute `probe` / `main`

- [ ] **Step 3: Write minimal implementation** (append)

```python
import argparse
import os
import subprocess
import time

# source -> (command, timeout seconds). Timeouts are well under the 5s tick (§4.2).
DAEMON_UNITS = {"cluster": ["corosync", "pacemaker"], "storage": ["drbd"]}
_COMMANDS = {
    "crm": (["crm_mon", "--one-shot", "--output-as=xml"], 3),
    "drbd": (["drbdsetup", "status", "--json"], 2),
    "stonith": (["stonith_admin", "--history", "*"], 2),
    "iscsi": (["iscsiadm", "-m", "session"], 2),
}


def probe(cmd: list[str], timeout: int) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE)."""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def collect(role: str, node: str, now: int) -> str:
    """Run this role's probe set and render the textfile body."""
    crm = stonith = iscsi = drbd = None
    daemons: dict[str, bool] = {}
    fresh: list[str] = []
    for source in PROBE_SETS[role]:
        if source == "daemons":
            units = DAEMON_UNITS[role]
            raw = probe(["systemctl", "is-active", *units], timeout=2)
            if raw is not None:
                daemons = parse_daemons(raw, units)
                fresh.append("daemons")
            continue
        cmd, timeout = _COMMANDS[source]
        raw = probe(cmd, timeout)
        if raw is None:
            continue
        if source == "crm":
            crm = parse_crm(raw)
        elif source == "drbd":
            drbd = parse_drbd(raw)
        elif source == "stonith":
            stonith = parse_stonith(raw)
        elif source == "iscsi":
            iscsi = parse_iscsi(raw)
        fresh.append(source)
    return render_cluster_state_prom(
        node=node, crm=crm, stonith=stonith, iscsi=iscsi, daemons=daemons,
        drbd=drbd, now=now, fresh_sources=tuple(fresh),
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the deployed collector. `lab-cluster-state --role {cluster,storage}`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=sorted(PROBE_SETS))
    ap.add_argument("--node", default=os.uname().nodename)
    ap.add_argument("--out", default="/var/lib/node_exporter/textfile/lab_cluster_state.prom")
    ap.add_argument("--now", type=int, default=None)
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    body = collect(args.role, args.node, now)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(body)
    os.replace(tmp, args.out)


if __name__ == "__main__":
    main()
```

> Note: `collect()` is exercised through `main()` in the storage test; add the small
> cluster-role test below so `collect`'s crm/stonith/iscsi branches are covered (the
> 100% branch gate requires it).

- [ ] **Step 4: Add the cluster-role coverage test**

```python
def test_main_cluster_role_runs_crm_stonith_iscsi(tmp_path, monkeypatch):
    crm_xml = (FIXTURES / "crm_mon.xml").read_text()
    def fake_probe(cmd, timeout):
        if cmd[0] == "crm_mon": return crm_xml
        if cmd[0] == "stonith_admin": return ""
        if cmd[0] == "iscsiadm": return "tcp: [1] 10.40.1.5:3260,1 iqn.lab:san-a\n"
        if cmd[0] == "systemctl": return "active\nactive\n"
        return None
    monkeypatch.setattr(clusterstate, "probe", fake_probe)
    out = tmp_path / "c.prom"
    clusterstate.main(["--role", "cluster", "--node", "pcmk-a1", "--out", str(out), "--now", "1781455000"])
    text = out.read_text()
    assert 'cluster_quorate{node="pcmk-a1"} 1' in text
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 1' in text


def test_main_marks_source_stale_when_probe_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(clusterstate, "probe", lambda cmd, timeout: None)  # everything times out
    out = tmp_path / "c.prom"
    clusterstate.main(["--role", "cluster", "--node", "pcmk-a1", "--out", str(out), "--now", "1781455000"])
    text = out.read_text()
    assert "cluster_quorate" not in text          # crm stale -> omitted
    assert "last_write_timestamp" not in text     # no source fresh
```

- [ ] **Step 5: Run to verify all pass**

Run: `uv run pytest tests/test_clusterstate.py -v`
Expected: PASS (all)

- [ ] **Step 6: Run the full gate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS, including 100% branch coverage on `clusterstate.py`. If coverage flags
an untested branch, add a test for it before committing.

- [ ] **Step 7: Commit**

```bash
vrg-git add src/mqlab/clusterstate.py tests/test_clusterstate.py
vrg-commit --type feat --scope obs --message "bounded probe runner + collector main (#177)"
```

---

### Task 8: Ansible `cluster-state` role — deploy collector + 5s timer

**Files:**
- Create: `ansible/roles/cluster-state/tasks/main.yml`
- Create: `ansible/roles/cluster-state/templates/lab-cluster-state.service.j2`
- Create: `ansible/roles/cluster-state/templates/lab-cluster-state.timer.j2`

The role mirrors `net-reach` (textfile dir → deploy collector → service+timer →
enable), but deploys the **Python module verbatim** as the collector and passes
`--role` per node group. It expects a `cluster_state_role` var (`cluster` or
`storage`) set by the playbook (Task 9).

- [ ] **Step 1: Write the service template**

```jinja
{# ansible/roles/cluster-state/templates/lab-cluster-state.service.j2 #}
[Unit]
Description=Render lab_cluster_state textfile ({{ cluster_state_role }} role)

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/lab-cluster-state --role {{ cluster_state_role }}
```

- [ ] **Step 2: Write the timer template** (5s cadence, §4.2)

```jinja
{# ansible/roles/cluster-state/templates/lab-cluster-state.timer.j2 #}
[Unit]
Description=Refresh lab_cluster_state every 5s

[Timer]
OnBootSec=5
OnUnitActiveSec=5
AccuracySec=1s

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write the role tasks**

```yaml
# ansible/roles/cluster-state/tasks/main.yml
---
- name: textfile dir
  ansible.builtin.file:
    path: /var/lib/node_exporter/textfile
    state: directory
    mode: "0755"

- name: deploy the cluster-state collector (the tested mqlab module, verbatim)
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../src/mqlab/clusterstate.py"
    dest: /usr/local/bin/lab-cluster-state
    mode: "0755"

- name: install service + timer
  ansible.builtin.template:
    src: "{{ item }}"
    dest: "/etc/systemd/system/{{ item | basename | regex_replace('\\.j2$', '') }}"
    mode: "0644"
  loop: [lab-cluster-state.service.j2, lab-cluster-state.timer.j2]

- name: enable + start the timer
  ansible.builtin.systemd:
    name: lab-cluster-state.timer
    enabled: true
    state: started
    daemon_reload: true
```

> The collector reads root-only tools (`crm_mon`, `drbdsetup`, `stonith_admin`); the
> timer runs as root (lab VM), matching `net-reach`. No `--node` is passed, so it
> defaults to `os.uname().nodename` — confirm in Task 10 that this equals the topology
> node name (e.g. `pcmk-a1`); if the guest hostname differs, add `--node {{ inventory_hostname }}`
> to the service `ExecStart`.

- [ ] **Step 4: Commit** (no unit tests — Ansible YAML; validated by `vrg-validate` lint + the live run in Task 10)

```bash
vrg-git add ansible/roles/cluster-state/
vrg-commit --type feat --scope obs --message "cluster-state collector role (deploy + 5s timer) (#177)"
```

---

### Task 9: Wire the role into the observability overlay

**Files:**
- Modify: `ansible/observability.yml`

The cluster-state collector is a guest textfile collector, so it belongs with its
siblings in `ansible/observability.yml` — the `hosts: all` obs overlay that already
applies `node-exporter` and `{role: net-reach, when: reach_peers | length > 0}` — **not**
in the cluster bring-up playbook. (Confirmed against current `develop` post-#179:
`site.yml` was removed; `site-distributed.yml` imports `site-pcmk.yml` for cluster
bring-up, while the obs overlay stays separate and runs on all guests.) The relevant
groups are `pcmk_a`/`pcmk_b` (cluster role) and `san_a`/`san_b` (storage role).

- [ ] **Step 1: Read the current overlay**

Run: `cat ansible/observability.yml`
Confirm the single `- hosts: all` play whose `roles:` includes `node-exporter` and the
conditional `{role: net-reach, when: reach_peers | length > 0}`.

- [ ] **Step 2: Add the two conditional cluster-state entries**

In that play's `roles:` list, after the `net-reach` line, add:

```yaml
    - role: cluster-state
      vars: {cluster_state_role: cluster}
      when: "'pcmk_a' in group_names or 'pcmk_b' in group_names"
    - role: cluster-state
      vars: {cluster_state_role: storage}
      when: "'san_a' in group_names or 'san_b' in group_names"
```

This runs the collector only on the cluster + SAN nodes, with the right `--role`, and
skips obs/app/dtcc — matching how `net-reach` is conditionally applied. The role's
`copy` src `{{ playbook_dir }}/../src/mqlab/clusterstate.py` resolves correctly because
`observability.yml` lives in `ansible/`.

- [ ] **Step 3: Lint-validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (ansible-lint clean on `observability.yml`).

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/observability.yml
vrg-commit --type feat --scope obs --message "apply cluster-state collector via the obs overlay (#177)"
```

---

### Task 10: Live verification on the running lab (acceptance)

**Files:** none (operational verification).

This proves the collector end-to-end. Requires the lab up (`pcmk_san_ha` /
`distributed` setup). Per the repo cold-rebuild gate, the real acceptance is a cold
rebuild + a drill; this task is the inner verification loop before that.

- [ ] **Step 1: Re-run Task 1 capture if it used sample fixtures**, replace the
  fixtures with real output, and re-run `uv run pytest tests/test_clusterstate.py -v`.
  Reconcile any parser mismatch (real schema vs sample) now — this is the spike
  closing the loop. Re-commit fixtures + any parser fixes.

- [ ] **Step 2: Provision the collector** (human-run, via the obs overlay from Task 9):

```bash
ansible-playbook ansible/observability.yml   # or `mqlab obs up`, which runs it
```

- [ ] **Step 3: Confirm the textfile exists and has fresh timestamps** on a pcmk node:

```bash
cat /var/lib/node_exporter/textfile/lab_cluster_state.prom
```
Expected: `cluster_quorate{...} 1`, `cluster_resource_owner{...,holder="pcmk-aN"} 1`,
and a recent `cluster_state_last_write_timestamp{...,source="crm"}`.

- [ ] **Step 4: Confirm Prometheus scrapes them** (no new job — the existing `node` job):

```bash
curl -s 'http://10.50.0.2:9090/api/v1/query?query=cluster_quorate' | python3 -m json.tool
curl -s 'http://10.50.0.2:9090/api/v1/query?query=cluster_drbd_out_of_sync_bytes' | python3 -m json.tool
```
Expected: series present, labelled by `node`.

- [ ] **Step 5: Prove fail-loud staleness.** Stop the timer on one pcmk node
  (`systemctl stop lab-cluster-state.timer`); after ~15s confirm that node's
  `cluster_state_last_write_timestamp{source="crm"}` stops advancing (the dashboard, in
  Plan 1b, renders this as STALE). Restart the timer.

- [ ] **Step 6: Prove the bound holds.** While watching, `systemctl stop corosync` on a
  pcmk node briefly: confirm `crm_mon` either returns the degraded view or times out,
  the collector still completes within its tick (the textfile keeps being rewritten,
  other sources stay fresh), and nothing hangs. Restore corosync.

- [ ] **Step 7: Record results** in the PR description (the metrics observed, the
  staleness behaviour). No commit — this is acceptance evidence for Plan 1a.

---

## Self-review

**Spec coverage (§4 of the design):**
- §4.1 cluster-node signals (quorum, node online/standby, resource placement, fence,
  iSCSI, daemons) → Tasks 3, 5, 6. ✓
- §4.1 SAN signals (DRBD role/disk/conn/resync%/out-of-sync) → Tasks 4, 6. ✓
- §4.2 cadence (~5s) → Task 8 timer. ✓
- §4.2 bounded/non-blocking probes, timeout → no fresh sample → STALE → Task 7
  (`probe` returns None; `fresh_sources` drops it) + tests. ✓
- §4.2 no-overlap (hung runs can't stack) → systemd `oneshot` + `OnUnitActiveSec`
  (a new run isn't scheduled until the prior `oneshot` exits) — Task 8; noted here so
  the engineer doesn't add a redundant lock.
- §4.3 redundant cluster-wide reporting + per-source `last_write_timestamp` → Task 6
  (every node emits cluster-wide facts; dashboard `max by(...)` is Plan 1b). ✓
- §4.3 source precedence (fenced ≠ blind) → **data side only here**: the collector
  emits both the authoritative `cluster_node_online/unclean` (from peers) and the
  per-source timestamps; the *rendering* of precedence is Plan 1b. Flagged so 1b
  implements the fold, not 1a.
- No new Prometheus job (rides `node` textfile) → Tasks 8/10. ✓

**Placeholder scan:** no TBD/TODO; every code step has complete code; fixtures task has
a real capture procedure with a fallback that is explicitly reconciled in Task 10.

**Type/name consistency:** `parse_crm`/`parse_drbd`/`parse_stonith`/`parse_iscsi`/
`parse_daemons` → consumed by `render_cluster_state_prom(node, crm, stonith, iscsi,
daemons, drbd, now, fresh_sources)` → driven by `collect(role, node, now)` →
`main(argv)`. Keyword names match across Tasks 3–7. `PROBE_SETS` (Task 2) drives
`collect` (Task 7). `cluster_state_role` var (Task 8) matches the playbook wiring
(Task 9).

**Out of scope (correctly deferred to later plans):** the Grafana matrix panel,
integrity alarm, and source-precedence *rendering* (Plan 1b); the overview roll-up
(Plan 1c); perf/network sections (Plan 2); logs (Plan 3).
