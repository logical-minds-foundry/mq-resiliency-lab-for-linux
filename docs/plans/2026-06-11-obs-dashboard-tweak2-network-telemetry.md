# Observability Dashboard — Tweak 2: Network Telemetry + Live Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** Tweak 1 (#108, the dashboard restructure) is merged — `render_dashboard` exists. This plan needs its **own issue + feature branch** off `develop`.

**Goal:** Light up the dashboard's network row with real data: a tri-state `lab_network_state` (absent/inactive/active) from a host-side collector, plus per-node `lab_net_reach` reachability — so breaking a network (down vs. destroy) and partial partitions are visible.

**Architecture:** Both new metric families ride `node_exporter` textfile collectors (first enable the collector in the role). The Vergil VM becomes the first host-side scrape target (`10.50.0.1`, label `host="hypervisor"`) via a one-line `render_scrape_targets` addition; on it, `mqlab obs net-state` (reusing `lifecycle.classify_net`) writes `lab_network_state`. Each guest runs a peer-ping timer writing `lab_net_reach`, peers derived from topology by a pure `net_peers()`. `render_dashboard` replaces the placeholder network row with real tri-state + reachability tiles.

**Tech Stack:** Python 3.12, `node_exporter` textfile collector, Ansible (incl. a `connection: local` host play), systemd timers, Grafana, pytest.

---

## Scope & boundaries

**In scope:** enable the textfile collector; `render_scrape_targets` host target; `lab_network_state` (host) + `lab_net_reach` (per-node) collectors; the real network row in `render_dashboard`.

**Out of scope:** the MQ row (Plan B). Splitting the network strip into per-site sub-groups (later, trivial).

**Verified facts (this branch):** `lifecycle.classify_net(states, net) -> ABSENT|INACTIVE|ACTIVE`; `netsel.parse_net_states(text)` parses `virsh net-list --all`; `netsel.lab_net_names()` lists declared networks from `lab/networks/net-*.xml`. `render_scrape_targets` (scrape.py) emits the node-job file_sd. The Vergil VM holds `10.50.0.1` on `net-mgmt` and runs `virsh`/`mqlab`.

---

### Task 1: Enable the `node_exporter` textfile collector (the prerequisite)

**Files:**
- Modify: `ansible/roles/node-exporter/templates/node_exporter.service.j2`
- Modify: `ansible/roles/node-exporter/tasks/main.yml`

- [ ] **Step 1: Add the textfile dir to the unit**

In `ansible/roles/node-exporter/templates/node_exporter.service.j2`, change the `ExecStart`:

```ini
ExecStart=/usr/local/bin/node_exporter \
  --web.listen-address=:9100 \
  --collector.textfile.directory=/var/lib/node_exporter/textfile
```

- [ ] **Step 2: Create the textfile dir before the service starts**

In `ansible/roles/node-exporter/tasks/main.yml`, add **before** the "install the systemd unit" task:

```yaml
- name: node_exporter textfile dir
  ansible.builtin.file:
    path: /var/lib/node_exporter/textfile
    state: directory
    owner: node_exporter
    group: node_exporter
    mode: "0755"
```

- [ ] **Step 3: Syntax-check + commit**

Run: `cd ansible && ansible-playbook --syntax-check site-obs.yml`
Expected: `playbook: site-obs.yml`, no error.

```bash
vrg-commit --type feat --scope ansible --message "node-exporter: enable the textfile collector (#<N>)" --body "Add --collector.textfile.directory=/var/lib/node_exporter/textfile and create the dir. Prerequisite for lab_network_state and lab_net_reach (and Plan C's QM-owner collector) — without it, textfile .prom output is never scraped."
```

---

### Task 2: `render_scrape_targets` gains the hypervisor host target (TDD)

**Files:**
- Modify: `src/mqlab/scrape.py`
- Test: `tests/test_scrape.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scrape.py`:

```python
def test_includes_the_hypervisor_host_target():
    import json

    from mqlab.scrape import HYPERVISOR_MGMT_IP, render_scrape_targets

    out = json.loads(render_scrape_targets({"nodes": {}, "groups": {}}))
    assert {"targets": [f"{HYPERVISOR_MGMT_IP}:9100"],
            "labels": {"host": "hypervisor", "groups": "hypervisor"}} in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_scrape.py::test_includes_the_hypervisor_host_target -v`
Expected: FAIL — `HYPERVISOR_MGMT_IP` undefined / target absent.

- [ ] **Step 3: Add the host target**

In `src/mqlab/scrape.py`, add the constant near `NODE_EXPORTER_PORT`:

```python
HYPERVISOR_MGMT_IP = "10.50.0.1"  # the Vergil VM (libvirt host) on net-mgmt
```

and in `render_scrape_targets`, append the synthetic entry before `return`:

```python
    entries.append({
        "targets": [f"{HYPERVISOR_MGMT_IP}:{NODE_EXPORTER_PORT}"],
        "labels": {"host": "hypervisor", "groups": "hypervisor"},
    })
    return json.dumps(entries, indent=2) + "\n"
```

- [ ] **Step 4: Run + fix the existing real-topology guard**

Run: `uv run pytest tests/test_scrape.py tests/test_topology_integrity.py -v`
Expected: PASS. (`test_real_topology_renders_scrape_targets` still holds — `obs`/`mon-probe` are still present; the hypervisor is an *extra* entry. If that test asserts an exact host *set*, relax it to `<=` / membership, which it already uses.)

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "scrape: add the hypervisor host target (10.50.0.1) (#<N>)" --body "render_scrape_targets emits a synthetic host=\"hypervisor\" target so Prometheus scrapes node_exporter on the Vergil VM (where lab_network_state lives). The only renderer change for host-side telemetry."
```

---

### Task 3: `lab_network_state` — pure renderer + `mqlab obs net-state` (TDD)

**Files:**
- Create: `src/mqlab/netstate.py`
- Test: `tests/test_netstate.py`
- Modify: `src/mqlab/cli.py` (+ `tests/test_cli_obs.py`)

- [ ] **Step 1: Write the failing test for the pure renderer**

Create `tests/test_netstate.py`:

```python
from __future__ import annotations

from mqlab.netstate import render_net_state_prom


def test_emits_tri_state_per_named_network():
    # parsed `virsh net-list --all` states; net-data-a undefined (absent)
    states = {"net-hb-a": "active", "net-wan": "inactive"}
    out = render_net_state_prom(["net-hb-a", "net-wan", "net-data-a"], states)
    assert 'lab_network_state{network="net-hb-a"} 2' in out   # active
    assert 'lab_network_state{network="net-wan"} 1' in out    # inactive
    assert 'lab_network_state{network="net-data-a"} 0' in out  # absent (not in states)
    assert out.startswith("# HELP lab_network_state")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_netstate.py -v`
Expected: FAIL — no module `mqlab.netstate`.

- [ ] **Step 3: Implement the pure renderer (reusing `classify_net`)**

Create `src/mqlab/netstate.py`:

```python
"""Render libvirt network state as a Prometheus textfile (#108, Tweak 2).

Tri-state per #107: absent(0)/inactive(1)/active(2), via lifecycle.classify_net,
over the topology-declared network list — so a destroyed (undefined) net still
gets an explicit 0, not a missing series.
"""

from __future__ import annotations

from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, classify_net

_CODE = {ABSENT: 0, INACTIVE: 1, ACTIVE: 2}


def render_net_state_prom(net_names: list[str], parsed_states: dict[str, str]) -> str:
    """Project (declared net names, parsed `virsh net-list --all`) -> textfile metrics."""
    lines = [
        "# HELP lab_network_state libvirt network state (0=absent,1=inactive,2=active)",
        "# TYPE lab_network_state gauge",
    ]
    for net in net_names:
        lines.append(f'lab_network_state{{network="{net}"}} {_CODE[classify_net(parsed_states, net)]}')
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_netstate.py -v`
Expected: PASS.

- [ ] **Step 5: Add the `mqlab obs net-state` CLI (the timer invokes it on the host)**

Write the failing test — append to `tests/test_cli_obs.py`:

```python
def test_obs_net_state_emits_textfile_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # one declared network
    (tmp_path / "lab" / "networks").mkdir(parents=True)
    (tmp_path / "lab" / "networks" / "net-hb-a.xml").write_text("<network/>")
    # virsh net-list --all reports it active
    runner = RecordingRunner(results=[ScriptedResult(
        [" Name      State    Autostart   Persistent",
         "----------------------------------------------",
         " net-hb-a   active   yes         yes"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "net-state"])

    assert result.exit_code == 0
    assert 'lab_network_state{network="net-hb-a"} 2' in result.stdout
```

Run: `uv run pytest tests/test_cli_obs.py::test_obs_net_state_emits_textfile_metrics -v` → FAIL (no command).

Add to `src/mqlab/cli.py` after `obs_dashboard`:

```python
_NET_LIST_ALL = Command([*_VIRSH, "net-list", "--all"])  # noqa: S607


@obs_app.command("net-state")
def obs_net_state() -> None:
    """Emit lab_network_state textfile metrics from `virsh net-list --all` (run on the host)."""
    from mqlab.netsel import lab_net_names, parse_net_states
    from mqlab.netstate import render_net_state_prom

    deps = build_deps("obs-net-state", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    captured: list[str] = []
    try:
        deps.runner.run(_NET_LIST_ALL, captured.append)
        states = parse_net_states("\n".join(captured))
        typer.echo(render_net_state_prom(lab_net_names(), states), nl=False)
    finally:
        deps.transcript.close()
```

Run: `uv run pytest tests/test_cli_obs.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "lab_network_state: tri-state net-state renderer + mqlab obs net-state (#<N>)" --body "Pure render_net_state_prom reuses lifecycle.classify_net over the declared net list (absent/inactive/active = 0/1/2). mqlab obs net-state runs virsh net-list --all and emits the textfile metrics; a host timer redirects it into the textfile dir."
```

---

### Task 4: Host collector wiring — node_exporter + net-state timer on the Vergil VM

**Files:**
- Create: `ansible/host-obs.yml`
- Create: `ansible/roles/host-net-state/tasks/main.yml`
- Create: `ansible/roles/host-net-state/templates/lab-net-state.service.j2`
- Create: `ansible/roles/host-net-state/templates/lab-net-state.timer.j2`
- Modify: `src/mqlab/cli.py` (`_obs_up_steps`) + `tests/test_cli_obs.py`

- [ ] **Step 1: The host net-state service/timer templates**

`ansible/roles/host-net-state/templates/lab-net-state.service.j2`:

```ini
[Unit]
Description=Render lab_network_state textfile

[Service]
Type=oneshot
WorkingDirectory={{ repo_root }}
ExecStart=/bin/sh -c 'mqlab obs net-state > /var/lib/node_exporter/textfile/lab_network_state.prom.tmp && mv /var/lib/node_exporter/textfile/lab_network_state.prom.tmp /var/lib/node_exporter/textfile/lab_network_state.prom'
```

`ansible/roles/host-net-state/templates/lab-net-state.timer.j2`:

```ini
[Unit]
Description=Refresh lab_network_state every 10s

[Timer]
OnBootSec=10
OnUnitActiveSec=10
AccuracySec=1s

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: The host role tasks** — `ansible/roles/host-net-state/tasks/main.yml`:

```yaml
---
- name: textfile dir for the host collector
  ansible.builtin.file:
    path: /var/lib/node_exporter/textfile
    state: directory
    mode: "0755"

- name: install the net-state service + timer
  ansible.builtin.template:
    src: "{{ item }}"
    dest: "/etc/systemd/system/{{ item | basename | regex_replace('\\.j2$', '') }}"
    mode: "0644"
  loop:
    - lab-net-state.service.j2
    - lab-net-state.timer.j2

- name: enable + start the net-state timer
  ansible.builtin.systemd:
    name: lab-net-state.timer
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 3: The local host play** — `ansible/host-obs.yml` (the Vergil VM runs node_exporter + the net-state collector; `connection: local`):

```yaml
# Host-side observability on the Vergil VM (the libvirt host). Run locally:
#   ansible-playbook host-obs.yml -c local -i localhost,
- hosts: localhost
  connection: local
  become: true
  vars:
    repo_root: "{{ playbook_dir }}/.."
  roles:
    - node-exporter
    - host-net-state
```

- [ ] **Step 4: `obs up` runs the host play** — update `_obs_up_steps` in `src/mqlab/cli.py` to append a host-provision step:

```python
        CommandStep(
            "provision host collector",
            Command(
                ["uv", "run", "ansible-playbook", "host-obs.yml", "-c", "local", "-i", "localhost,"],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
```

Update `tests/test_cli_obs.py::test_obs_up_renders_then_creates_then_provisions` to expect the extra step:

```python
    assert any(a[:3] == ["uv", "run", "ansible-playbook"] and "host-obs.yml" in a for a in argvs)
```

and extend the `RecordingRunner` results list in that test by one more `ScriptedResult(["ok"])` so every step has a scripted result.

Run: `uv run pytest tests/test_cli_obs.py -v` → PASS.

- [ ] **Step 5: Syntax-check + commit**

Run: `cd ansible && ansible-playbook --syntax-check host-obs.yml -i localhost,`
Expected: `playbook: host-obs.yml`.

```bash
vrg-commit --type feat --scope ansible --message "host-obs: node_exporter + lab_network_state timer on the Vergil VM (#<N>)" --body "A connection=local play installs node_exporter (textfile collector) + a 10s timer running mqlab obs net-state into the textfile dir. obs up runs it, so Prometheus scrapes the hypervisor target with live tri-state network metrics."
```

---

### Task 5: `lab_net_reach` — pure `net_peers` + per-node peer-ping (TDD)

**Files:**
- Modify: `src/mqlab/netstate.py` (add `net_peers`)
- Test: `tests/test_netstate.py`
- Modify: `src/mqlab/cli.py` (`mqlab obs reach-peers`) + `tests/test_cli_obs.py`
- Modify: `ansible/observability.yml`
- Create: `ansible/roles/net-reach/tasks/main.yml`, `templates/lab-net-reach.sh.j2`, `templates/lab-net-reach.service.j2`, `templates/lab-net-reach.timer.j2`

- [ ] **Step 1: Failing test for `net_peers`**

Append to `tests/test_netstate.py`:

```python
def test_net_peers_lists_same_network_peers_with_their_ips():
    from mqlab.netstate import net_peers

    topo = {"nodes": {
        "pcmk-a1": {"nics": {"net-hb-a": "172.16.1.51", "net-mgmt": "10.50.0.51"}},
        "pcmk-a2": {"nics": {"net-hb-a": "172.16.1.52", "net-mgmt": "10.50.0.52"}},
        "san-a":   {"nics": {"net-san-a": "10.40.1.5"}},
    }}
    peers = net_peers(topo)
    assert peers["pcmk-a1"]["net-hb-a"] == [{"peer": "pcmk-a2", "ip": "172.16.1.52"}]
    # net-mgmt is excluded (the scrape plane, not a tested data path)
    assert "net-mgmt" not in peers["pcmk-a1"]
    # a node alone on a net has no peers entry for it
    assert peers.get("san-a", {}) == {}
```

- [ ] **Step 2: Run it** → FAIL (no `net_peers`).

- [ ] **Step 3: Implement `net_peers`** — append to `src/mqlab/netstate.py`:

```python
from typing import Any

EXCLUDED_NETS = {"net-mgmt"}  # the scrape plane itself — never a tested data path


def net_peers(topo: dict[str, Any]) -> dict[str, dict[str, list[dict[str, str]]]]:
    """host -> network -> [{peer, ip}] for every same-network peer (mgmt excluded)."""
    nodes = topo.get("nodes", {})
    # network -> [(host, ip)]
    members: dict[str, list[tuple[str, str]]] = {}
    for host, spec in nodes.items():
        for net, ip in ((spec or {}).get("nics") or {}).items():
            if net in EXCLUDED_NETS:
                continue
            members.setdefault(net, []).append((host, str(ip)))
    result: dict[str, dict[str, list[dict[str, str]]]] = {}
    for net, hosts in members.items():
        for host, _ in hosts:
            peers = [{"peer": p, "ip": pip} for p, pip in hosts if p != host]
            if peers:
                result.setdefault(host, {})[net] = peers
    return result
```

(Place the `from typing import Any` import with the existing imports at the top; don't duplicate.)

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: `mqlab obs reach-peers` writes the per-host peer map to build/**

Failing test — append to `tests/test_cli_obs.py`:

```python
def test_obs_reach_peers_writes_build_json(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  pcmk-a1: {nics: {net-hb-a: 172.16.1.51}}\n"
        "  pcmk-a2: {nics: {net-hb-a: 172.16.1.52}}\n"
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "reach-peers"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / "build" / "obs" / "reach-peers.json").read_text())
    assert data["pcmk-a1"]["net-hb-a"][0]["peer"] == "pcmk-a2"
```

Run it → FAIL. Then add to `src/mqlab/cli.py` after `obs_net_state`:

```python
@obs_app.command("reach-peers")
def obs_reach_peers() -> None:
    """Render build/obs/reach-peers.json (host -> net -> peers) from topology."""
    import json as _json

    import yaml as _yaml

    from mqlab.netstate import net_peers

    deps = build_deps("obs-reach-peers", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        topo = _yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
        path = repo_root() / "build" / "obs" / "reach-peers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(net_peers(topo), indent=2) + "\n")
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
    finally:
        deps.transcript.close()
```

Run `uv run pytest tests/test_cli_obs.py -v` → PASS.

- [ ] **Step 6: The per-node ping script + timer** (peers injected via `host_vars` from the rendered JSON).

`ansible/roles/net-reach/templates/lab-net-reach.sh.j2` (renders this host's peers in; pings; writes textfile atomically; emits a freshness stamp so an unrunnable probe reads stale, never green):

```bash
#!/usr/bin/env bash
set -euo pipefail
OUT=/var/lib/node_exporter/textfile/lab_net_reach.prom
TMP="${OUT}.tmp"
{
  echo "# HELP lab_net_reach 1 if peer reachable on the network, else 0"
  echo "# TYPE lab_net_reach gauge"
{% for net, peers in (reach_peers | default({})).items() %}
{% for p in peers %}
  if ping -c1 -W1 {{ p.ip }} >/dev/null 2>&1; then R=1; else R=0; fi
  echo "lab_net_reach{network=\"{{ net }}\",peer=\"{{ p.peer }}\"} ${R}"
{% endfor %}
{% endfor %}
  echo "# TYPE lab_net_reach_last_write_timestamp gauge"
  echo "lab_net_reach_last_write_timestamp $(date +%s)"
} > "${TMP}"
mv "${TMP}" "${OUT}"
```

`ansible/roles/net-reach/templates/lab-net-reach.service.j2`:

```ini
[Unit]
Description=Render lab_net_reach textfile

[Service]
Type=oneshot
ExecStart=/usr/local/bin/lab-net-reach.sh
```

`ansible/roles/net-reach/templates/lab-net-reach.timer.j2`:

```ini
[Unit]
Description=Refresh lab_net_reach every 15s

[Timer]
OnBootSec=15
OnUnitActiveSec=15
AccuracySec=1s

[Install]
WantedBy=timers.target
```

`ansible/roles/net-reach/tasks/main.yml`:

```yaml
---
- name: textfile dir
  ansible.builtin.file:
    path: /var/lib/node_exporter/textfile
    state: directory
    mode: "0755"

- name: deploy the peer-ping script (this host's peers baked in)
  ansible.builtin.template:
    src: lab-net-reach.sh.j2
    dest: /usr/local/bin/lab-net-reach.sh
    mode: "0755"

- name: install service + timer
  ansible.builtin.template:
    src: "{{ item }}"
    dest: "/etc/systemd/system/{{ item | basename | regex_replace('\\.j2$', '') }}"
    mode: "0644"
  loop: [lab-net-reach.service.j2, lab-net-reach.timer.j2]

- name: unprivileged ICMP for the ping (no setuid needed)
  ansible.posix.sysctl:
    name: net.ipv4.ping_group_range
    value: "0 2147483647"
    sysctl_set: true
    reload: true

- name: enable + start the timer
  ansible.builtin.systemd:
    name: lab-net-reach.timer
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 7: Wire it into `observability.yml`** with per-host peers from the rendered JSON:

```yaml
# Fleet-wide host metrics (#103) + reachability (#108). Render peers first:
#   mqlab obs reach-peers
- hosts: all
  become: true
  vars:
    reach_all: "{{ lookup('file', playbook_dir + '/../build/obs/reach-peers.json') | from_json }}"
    reach_peers: "{{ reach_all[inventory_hostname] | default({}) }}"
  roles:
    - node-exporter
    - { role: net-reach, when: reach_peers | length > 0 }
```

- [ ] **Step 8: Syntax-check + commit**

Run: `cd ansible && ansible-playbook --syntax-check observability.yml`
Expected: `playbook: observability.yml`.

```bash
vrg-commit --type feat --scope ansible --message "lab_net_reach: per-node peer-ping reachability (#<N>)" --body "Pure net_peers(topo) -> host->net->peers (mgmt excluded); mqlab obs reach-peers renders it to build/. The net-reach role bakes each host's peers into a ping-timer textfile, sets ping_group_range for unprivileged ICMP, and emits a last_write_timestamp so an unrunnable probe reads stale, not green. observability.yml applies it per host."
```

---

### Task 6: The real network row in `render_dashboard` (TDD)

> **REVISED (brainstormed mid-build):** the flat two-panel approach below was
> superseded by the **per-network-rows** design — three collapsible section rows
> (Message path / Cluster + storage / Cross-site + mgmt), each net a row of a
> **folded-health tile** (`lab_network_health`, grey/red/amber/green via a new
> Prometheus recording rule joining state + reachability) **+ own-scale rx/tx
> graphs** off the host bridge (`virbr-<x>`, existing `node_network_*` metrics —
> no new telemetry), shorthand names. See spec §3.2/§4. A `NET_SECTIONS` guard
> test asserts the curated list matches the declared `net-*.xml`. Implemented in
> the "per-network rows" commit; the original two-panel steps below are retained
> for history.

**Files:**
- Modify: `src/mqlab/dashboard.py`
- Modify: `ansible/roles/prometheus/` (recording rule + config)
- Test: `tests/test_dashboard.py`, `tests/test_topology_integrity.py`

The Networks row gets **two side-by-side panels** — a tri-state **state** panel
(`lab_network_state`) and a **reachability** panel (`lab_net_reach`) — so both
halves the spec calls for (§4) are emitted by `render_dashboard` *and tested*,
not deferred. (A single combined-color tile is a later polish; surfacing
reachability in a committed, tested panel is the requirement.)

- [ ] **Step 1: Failing tests — both network panels**

Append to `tests/test_dashboard.py`:

```python
def test_network_state_panel_has_tristate_mapping():
    panels = render_dashboard(TOPO)["panels"]
    net = next(p for p in panels if p.get("title") == "Networks — state")
    assert net["targets"][0]["expr"] == "lab_network_state"
    texts = {m["options"][k]["text"] for m in net["fieldConfig"]["defaults"]["mappings"]
             for k in m["options"]}
    assert {"ABSENT", "DOWN", "UP"} <= texts


def test_network_reachability_panel_rolls_up_per_network():
    panels = render_dashboard(TOPO)["panels"]
    reach = next(p for p in panels if p.get("title") == "Networks — reachability")
    # a network with ANY unreachable peer rolls up to 0 (per-network minimum)
    assert reach["targets"][0]["expr"] == "min by (network) (lab_net_reach)"
    texts = {m["options"][k]["text"] for m in reach["fieldConfig"]["defaults"]["mappings"]
             for k in m["options"]}
    assert {"UNREACHABLE", "REACHABLE"} <= texts
```

- [ ] **Step 2: Run it** → FAIL (placeholder text panel; no reachability panel).

- [ ] **Step 3: Replace the placeholder network section** in `src/mqlab/dashboard.py`. Swap the two placeholder lines:

```python
    panels.append(_row("Networks", y)); y += 1
    panels.append(_text("Network status arrives in **Tweak 2** (#108 follow-up).", y)); y += 3
```

with:

```python
    panels.append(_row("Networks", y)); y += 1
    panels.append(_network_state_panel(y))
    panels.append(_network_reach_panel(y)); y += 4
```

and add the two helpers:

```python
def _network_state_panel(y: int) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": "Networks — state",
        "gridPos": {"h": 4, "w": 12, "x": 0, "y": y},
        "fieldConfig": {"defaults": {"mappings": [
            {"type": "value", "options": {
                "0": {"text": "ABSENT", "color": "grey"},
                "1": {"text": "DOWN", "color": "red"},
                "2": {"text": "UP", "color": "green"},
            }}
        ]}},
        "targets": [{"expr": "lab_network_state", "legendFormat": "{{network}}"}],
    }


def _network_reach_panel(y: int) -> dict[str, Any]:
    # min by (network): a net with ANY unreachable peer rolls up to 0 (amber).
    return {
        "type": "stat",
        "title": "Networks — reachability",
        "gridPos": {"h": 4, "w": 12, "x": 12, "y": y},
        "fieldConfig": {"defaults": {"mappings": [
            {"type": "value", "options": {
                "0": {"text": "UNREACHABLE", "color": "orange"},
                "1": {"text": "REACHABLE", "color": "green"},
            }}
        ]}},
        "targets": [{"expr": "min by (network) (lab_net_reach)", "legendFormat": "{{network}}"}],
    }
```

This makes the spec's "active-but-unreachable" visible: a net stays green in the
**state** panel (it's active) while its **reachability** panel tile goes orange —
the subtle fault surfaced, in a tested artifact. A net with no peers (e.g.
`net-mgmt` is excluded; a single-member net) simply has no `lab_net_reach`
series and no reachability tile, which is correct.

- [ ] **Step 4: Run it** → PASS. Then `uv run pytest tests/test_dashboard.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "dashboard: real network row — state + reachability (#<N>)" --body "render_dashboard's Networks row emits two tested panels: a tri-state state tile (lab_network_state: absent/grey, down/red, up/green) and a per-network reachability roll-up (min by (network) lab_net_reach: orange when any peer unreachable). Replaces the Tweak-1 placeholder and surfaces the collected lab_net_reach so it isn't orphaned (spec 4 / criterion 4)."
```

---

### Task 7: Validate, live-verify, PR

- [ ] **Step 1: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS at 100% branch coverage (`netstate.py` fully exercised by `tests/test_netstate.py`; new CLI commands by `tests/test_cli_obs.py`; `dashboard.py` state + reachability panels by `tests/test_dashboard.py`).

- [ ] **Step 2: Live — render, provision, break a net, watch the tile**

```bash
mqlab obs reach-peers          # render the peer map
mqlab obs up                   # renders dashboard+targets+inventory, provisions host + pair
mqlab vm up pcmk_a && cd ansible && ansible-playbook observability.yml --limit pcmk_a && cd ..
# watch http://localhost:3000/d/lab-fleet-node :
mqlab net down net-hb-a        # hb-a tile -> red (DOWN) within a scrape interval
mqlab net destroy net-hb-a     # hb-a tile -> grey (ABSENT) — distinct from down
mqlab net up net-hb-a          # back to green (UP)
```
Expected: the **state** panel shows every declared net (`down` reads red, `destroy` reads grey, `up` reads green); and with `pcmk_a` up, the **reachability** panel shows hb-a REACHABLE — then `mqlab vm down pcmk-a2` flips hb-a's reachability tile to orange (UNREACHABLE) while its state tile stays green (still active), proving the active-but-unreachable case.

- [ ] **Step 3: PR via the issue-implement oracle** (own issue/branch):

```bash
vrg-pr-workflow next --issue <N> --no-audit
vrg-pr-workflow report-ready --title "feat(obs): network telemetry + live network panel (Tweak 2) (#<N>)" \
  --summary "Host tri-state lab_network_state collector + per-node lab_net_reach peer-ping + the hypervisor scrape target, wired into the dashboard's network row (green/red/grey + reachability)." \
  --notes "Tweak 2 of the #108 design. Depends on Tweak 1 (render_dashboard). If the PR conflicts with develop: fetch, rebase origin/develop, re-validate, push --force-with-lease."
vrg-pr-workflow next   # -> DONE; human runs vrg-submit-pr
```

---

## Self-review notes

- **Spec coverage:** §3.1 textfile prerequisite → Task 1; §3.4 host target → Task 2; §3.2 tri-state host collector → Tasks 3–4 (`render_net_state_prom` reuses `classify_net`, iterates declared nets); §3.3 reachability + privilege + fail-loud → Task 5 (`net_peers`, `ping_group_range`, `last_write_timestamp`); §4 network state+reachability → Task 6 — **two tested panels** (`lab_network_state` tri-state grey/red/green + `lab_net_reach` per-network roll-up that goes orange when any peer is unreachable), so the collected reachability metric is surfaced, not orphaned; §7 fail-loud / no-new-jobs → throughout (textfile on the node job).
- **Placeholder scan:** none. Both network panels are fully specified and tested in `render_dashboard`; a single combined-color tile is noted as optional later polish, but the reachability requirement is delivered by the committed `Networks — reachability` panel.
- **Type consistency:** `render_net_state_prom(net_names, parsed_states)`, `net_peers(topo)`, `HYPERVISOR_MGMT_IP`, the `mqlab obs net-state`/`reach-peers` verbs, and `lab_network_state`/`lab_net_reach` metric names are used identically across tasks. Reuses existing `classify_net`, `parse_net_states`, `lab_net_names` verbatim.
- **`<N>` is the Tweak-2 issue number** — opened when this plan starts (Tweak 2 is its own issue/PR off `develop`, after Tweak 1 merges).
