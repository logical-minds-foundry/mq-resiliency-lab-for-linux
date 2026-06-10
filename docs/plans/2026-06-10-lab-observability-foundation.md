# Lab Observability Foundation (Layers 0–1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a dedicated `obs` VM (Prometheus + Grafana) and a `mon-probe` VM, scraping `node_exporter` across the fleet over the existing `net-mgmt` plane, so a person can open a URL and watch the whole fleet live.

**Architecture:** Two new topology nodes (`obs`, `mon-probe`) in a `monitoring` setup. A pure `render_scrape_targets(topo)` renderer — sibling of the #104 `render_inventory` — projects `topology.yaml` to a Prometheus `file_sd` target list keyed on each host's `net-mgmt` IP, fail-loud on any missing IP. An `mqlab obs` CLI slice renders the targets and brings the monitoring pair up. Ansible roles install `node_exporter` (fleet-wide), Prometheus and Grafana (on `obs`), all provisioned as code. This plan delivers **Layers 0–1** of `docs/specs/2026-06-10-lab-observability-design.md`; MQ/cluster metrics (Layer 2) and the DR-state view (Layer 3) are separate plans.

**Tech Stack:** Python 3.12 (Typer CLI, PyYAML), Prometheus `node_exporter` + Prometheus server (pinned static Go binaries), Grafana OSS (apt), Ansible, Vagrant/libvirt, pytest.

---

## Scope & boundaries

**In scope (Layers 0–1):** `obs` + `mon-probe` nodes; `render_scrape_targets`; `mqlab obs targets|up|status|open`; `node-exporter`, `prometheus`, `grafana` Ansible roles; `site-obs.yml` + `observability.yml` playbooks; a fleet-health Grafana dashboard; the Layer-0 access proof.

**Out of scope (later plans):** `mq_prometheus` (local + client-mode), QM-side enablement, `ha_cluster_exporter`, the QM-ownership collector, the DR-state dashboard, Loki/Promtail timeline narration. The `mon-probe` node is **created** here (so it exists and is monitored) but carries **no MQ exporters yet** — those land in Plan B.

**Assumption to confirm in Task 10 (Layer-0 spike):** lab guests have outbound internet during provisioning (the existing `mq-client` role runs `apt-get update`/`install`, so apt works) — used to fetch the `node_exporter`/Prometheus binaries and the Grafana apt package. If a guest is offline, the spike fails loud and we pivot to pre-staging the binaries under `build/`.

**Pinned versions (used verbatim below):** `node_exporter` **1.8.2**, Prometheus **2.53.2** (LTS), Grafana OSS via the official apt repo. Arch map: `ansible_architecture` `aarch64`→`arm64`, `x86_64`→`amd64`.

---

### Task 1: Add `obs` + `mon-probe` nodes, `obs`/`probe` groups, `monitoring` setup

**Files:**
- Modify: `lab/topology.yaml`

- [ ] **Step 1: Add the two nodes to the `nodes:` map**

In `lab/topology.yaml`, after the `app-client` node (end of the Phase-B block), add:

```yaml
  # --- Observability (#103): obs runs Prometheus+Grafana on the mgmt plane only;
  # mon-probe carries net-data NICs for the (Plan B) client-mode MQ exporters. ---
  obs:
    cpus: 2
    memory: 4096
    nics: { net-mgmt: 10.50.0.2 }
  mon-probe:
    cpus: 1
    memory: 1024
    nics: { net-mgmt: 10.50.0.3, net-data-a: 10.10.1.7, net-data-b: 10.10.2.7 }
```

- [ ] **Step 2: Add the atomic groups**

In the `groups:` block, after `client: [app-client]`, add (the group is named
`obs_box`, **not** `obs`, so it never collides with the host named `obs` —
Ansible warns on a group and host sharing a name):

```yaml
  obs_box: [obs]
  probe:   [mon-probe]
```

- [ ] **Step 3: Add the `monitoring` setup**

In the `setups:` block, after `standalone:`, add:

```yaml
  monitoring:
    description: Observability pair — Prometheus/Grafana (obs) + the MQ client probe (mon-probe)
    groups: [obs_box, probe]
    provision: ansible/site-obs.yml
```

- [ ] **Step 4: Verify the real topology still renders a valid inventory**

Run: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-103-observability && uv run pytest tests/test_topology_integrity.py -v`
Expected: PASS — `obs`, `probe`, and `monitoring` carry net-mgmt IPs, so `render_inventory` raises nothing.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope lab --message "add obs + mon-probe nodes and the monitoring setup (#103)" --body "obs (mgmt-only, Prometheus/Grafana) and mon-probe (net-data NICs for Plan-B client-mode MQ exporters) as topology nodes; new obs/probe groups; monitoring setup provisioned by ansible/site-obs.yml."
```

---

### Task 2: The `render_scrape_targets` renderer (pure, fail-loud)

**Files:**
- Create: `src/mqlab/scrape.py`
- Test: `tests/test_scrape.py`

This mirrors `src/mqlab/inventory.py`: a pure projection of parsed topology, keyed on the `net-mgmt` IP, raising on any integrity problem. It emits a Prometheus `file_sd` JSON list for the `node` job (one entry per node, port 9100, labelled with the host name and the groups it belongs to).

- [ ] **Step 1: Write the failing test**

Create `tests/test_scrape.py`:

```python
from __future__ import annotations

import json

import pytest

from mqlab.scrape import ScrapeError, render_scrape_targets

TOPO = {
    "nodes": {
        "obs": {"nics": {"net-mgmt": "10.50.0.2"}},
        "qm-main": {"nics": {"net-mgmt": "10.50.0.10"}},
        "rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}},
    },
    "groups": {
        "obs": ["obs"],
        "qm": ["qm-main"],
        "rdqm_a": ["rdqm-a1"],
    },
}


def test_render_emits_file_sd_one_entry_per_node_on_9100():
    out = json.loads(render_scrape_targets(TOPO))
    assert out == [
        {"targets": ["10.50.0.2:9100"], "labels": {"host": "obs", "groups": "obs"}},
        {"targets": ["10.50.0.10:9100"], "labels": {"host": "qm-main", "groups": "qm"}},
        {"targets": ["10.50.0.31:9100"], "labels": {"host": "rdqm-a1", "groups": "rdqm_a"}},
    ]


def test_node_in_multiple_groups_joins_labels_sorted():
    topo = {
        "nodes": {"n1": {"nics": {"net-mgmt": "10.50.0.9"}}},
        "groups": {"z_grp": ["n1"], "a_grp": ["n1"]},
    }
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"]["groups"] == "a_grp,z_grp"


def test_node_in_no_group_gets_empty_groups_label():
    topo = {"nodes": {"lonely": {"nics": {"net-mgmt": "10.50.0.8"}}}, "groups": {}}
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"] == {"host": "lonely", "groups": ""}


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"bad": {"nics": {}}}, "groups": {}}
    with pytest.raises(ScrapeError, match="no net-mgmt IP: bad"):
        render_scrape_targets(topo)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mqlab.scrape'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/mqlab/scrape.py`:

```python
"""Render Prometheus file_sd scrape targets as a pure function of lab/topology.yaml (#103).

Sibling of inventory.py: one source of truth (topology), one address plan (the
net-mgmt IP). Emits the `node` job target list (node_exporter on :9100). MQ and
ha_cluster jobs are added by later plans (Layer 2). Fail-loud — a node without a
net-mgmt IP is an error, never a silent skip.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

NODE_EXPORTER_PORT = 9100


class ScrapeError(RuntimeError):
    """topology.yaml cannot be rendered to valid scrape targets."""


def _mgmt_ip(spec: dict[str, Any], host: str) -> str:
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise ScrapeError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def _groups_of(groups: dict[str, list[str]], host: str) -> str:
    return ",".join(sorted(g for g, hosts in groups.items() if host in hosts))


def render_scrape_targets(topo: dict[str, Any]) -> str:
    """Project parsed topology -> Prometheus file_sd JSON for the node job."""
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    entries = [
        {
            "targets": [f"{_mgmt_ip(spec or {}, host)}:{NODE_EXPORTER_PORT}"],
            "labels": {"host": host, "groups": _groups_of(groups, host)},
        }
        for host, spec in nodes.items()
    ]
    return json.dumps(entries, indent=2) + "\n"


def scrape_targets_path() -> Path:
    """Where the rendered node target list is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "prometheus" / "targets" / "node.json"


def lab_scrape_targets() -> str:
    """Render the real lab/topology.yaml to file_sd JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_scrape_targets(topo)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "render Prometheus scrape targets from topology (#103)" --body "Pure render_scrape_targets(topo) sibling of render_inventory: file_sd JSON for the node job, keyed on the net-mgmt IP, fail-loud on a missing IP. Adds scrape_targets_path()/lab_scrape_targets()."
```

---

### Task 3: Guard the real topology renders valid scrape targets

**Files:**
- Modify: `tests/test_topology_integrity.py`

- [ ] **Step 1: Add the failing guard test**

Append to `tests/test_topology_integrity.py`:

```python
def test_real_topology_renders_scrape_targets():
    import json

    from mqlab.scrape import lab_scrape_targets

    entries = json.loads(lab_scrape_targets())  # raises ScrapeError on any missing mgmt IP
    hosts = {e["labels"]["host"] for e in entries}
    assert {"obs", "mon-probe"} <= hosts
    assert all(e["targets"][0].endswith(":9100") for e in entries)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_topology_integrity.py -v`
Expected: PASS — every node (incl. `obs`/`mon-probe`) has a net-mgmt IP, so the render succeeds and both appear.

- [ ] **Step 3: Commit**

```bash
vrg-commit --type test --scope lab --message "guard the real topology renders valid scrape targets (#103)" --body "CI now catches a node added without a net-mgmt IP (would break Prometheus scraping), mirroring the inventory guard."
```

---

### Task 4: `mqlab obs targets` — render the target file (CLI)

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_obs.py`

This mirrors `vm inventory` (cli.py:334–350): render to the gitignored `build/` tree and echo it (treatment-A transparency).

- [ ] **Step 1: Write the failing CLI test**

Create `tests/test_cli_obs.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from mqlab import cli

runner = CliRunner()


def test_obs_targets_writes_and_echoes(monkeypatch, tmp_path):
    # Point repo_root at a tmp repo carrying a minimal topology.
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "groups:\n"
        "  obs: [obs]\n"
    )
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("mqlab.scrape.repo_root", lambda: tmp_path)

    result = runner.invoke(cli.app, ["obs", "targets"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "prometheus" / "targets" / "node.json"
    assert json.loads(written.read_text())[0]["labels"]["host"] == "obs"
    assert "10.50.0.2:9100" in result.stdout
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli_obs.py -v`
Expected: FAIL — `No such command 'obs'`.

- [ ] **Step 3: Add the `obs` Typer app and the `targets` command**

In `src/mqlab/cli.py`, after the `vm_app` block (after cli.py:151 `app.add_typer(vm_app, name="vm")`), add:

```python
obs_app = typer.Typer(help="observability stack (Prometheus + Grafana)", no_args_is_help=True)
app.add_typer(obs_app, name="obs")


@obs_app.command("targets")
def obs_targets() -> None:
    """Render build/prometheus/targets/node.json from topology and echo it."""
    from mqlab.scrape import lab_scrape_targets, scrape_targets_path

    deps = build_deps("obs-targets", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_scrape_targets()
        path = scrape_targets_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli_obs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "add mqlab obs targets (#103)" --body "Render build/prometheus/targets/node.json from topology and echo it, mirroring mqlab vm inventory."
```

---

### Task 5: `mqlab obs up | status | open` (CLI)

**Files:**
- Modify: `src/mqlab/cli.py`
- Create: `lab/scripts/obs-open.sh`
- Test: `tests/test_cli_obs.py`

`obs up` is a composite verb: render targets, then create/start the monitoring pair, then provision via Ansible. `obs status` filters the fleet to the `monitoring` setup. `obs open` prints the Grafana URL + the tunnel one-liner.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_obs.py`:

```python
def test_obs_open_prints_url_and_tunnel(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    result = runner.invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert "http://10.50.0.2:3000" in result.stdout
    assert "ssh -L" in result.stdout


def test_obs_up_steps_render_then_create_then_provision():
    steps = cli._obs_up_steps()
    labels = [s.label for s in steps]
    assert labels == ["render scrape targets", "monitoring create", "provision monitoring"]
    # provisioning targets the monitoring setup's playbook via the rendered inventory
    provision = steps[-1].command.argv
    assert "ansible-playbook" in provision
    assert "ansible/site-obs.yml" in " ".join(provision)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli_obs.py -k "obs_open or obs_up" -v`
Expected: FAIL — `_obs_up_steps`/`open` command not defined.

- [ ] **Step 3: Implement the three commands**

`CommandStep(label, command)` is a frozen two-field dataclass with no `pre` hook (confirmed in `src/mqlab/orchestrator.py`), so `_obs_up_steps` renders the targets **eagerly** when the steps are built, then emits an echo step recording it. In `src/mqlab/cli.py`, add a module-level constant near the top (after `_VIRSH` at cli.py:154) and the commands after `obs_targets`:

```python
GRAFANA_URL = "http://10.50.0.2:3000"  # obs net-mgmt IP : Grafana port


def _obs_up_steps() -> list[CommandStep]:
    from mqlab.scrape import lab_scrape_targets, scrape_targets_path

    path = scrape_targets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lab_scrape_targets())  # render eagerly when the steps are built

    return [
        CommandStep("render scrape targets", Command(["echo", f"rendered -> {path}"])),  # noqa: S607
        CommandStep(
            "monitoring create",
            Command(["vagrant", "up", "obs", "mon-probe"], cwd=repo_root() / "lab"),  # noqa: S607
        ),
        CommandStep(
            "provision monitoring",
            # bare filename, run from ansible/ so ansible.cfg (inventory) is picked
            # up — matches dr-provision.sh.
            Command(
                ["uv", "run", "ansible-playbook", "site-obs.yml"],  # noqa: S607
                cwd=repo_root() / "ansible",
            ),
        ),
    ]


@obs_app.command("up")
def obs_up(step: _StepFlag = False) -> None:
    """Render targets, create the monitoring pair, and provision Prometheus + Grafana."""
    _execute("obs-up", _obs_up_steps(), step_mode=step)


@obs_app.command("status")
def obs_status() -> None:
    """Show the monitoring pair's state (topology joined with live virsh state)."""
    guests = _resolve_or_exit("monitoring", resolve_guests, "guest")
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("obs-status", timestamp)
    try:
        code = vm_status_core(deps.runner, deps.renderer, deps.transcript, guests=guests)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


@obs_app.command("open")
def obs_open() -> None:
    """Print the Grafana URL and the SSH tunnel to reach it from your workstation."""
    typer.echo(f"Grafana: {GRAFANA_URL}")
    typer.echo("From inside the Vergil VM session this URL is directly reachable.")
    typer.echo("From your workstation, tunnel through the session host:")
    typer.echo(f"  ssh -L 3000:10.50.0.2:3000 <vergil-vm-session-host>  # then open {GRAFANA_URL}")
```

Note: the Task-5 Step-1 test asserts `steps[-1].command.argv` — matching `CommandStep.command` and `Command.argv` verbatim.

- [ ] **Step 4: Create the tunnel helper script (referenced in docs; keeps the one-liner copy-pasteable)**

Create `lab/scripts/obs-open.sh`:

```bash
#!/usr/bin/env bash
# obs-open.sh — print the Grafana URL and a ready tunnel command (#103).
set -euo pipefail
URL="http://10.50.0.2:3000"
echo "Grafana: ${URL}"
echo "Tunnel from your workstation: ssh -L 3000:10.50.0.2:3000 <vergil-vm-session-host>"
```

Make it executable: `chmod +x lab/scripts/obs-open.sh`

- [ ] **Step 5: Run the obs CLI tests**

Run: `uv run pytest tests/test_cli_obs.py -v`
Expected: PASS (all obs tests).

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "add mqlab obs up/status/open (#103)" --body "obs up renders targets, creates the monitoring pair, and provisions site-obs.yml; obs status filters the fleet to the monitoring setup; obs open prints the Grafana URL + SSH tunnel."
```

---

### Task 6: `node-exporter` Ansible role (fleet-wide host metrics)

**Files:**
- Create: `ansible/roles/node-exporter/tasks/main.yml`
- Create: `ansible/roles/node-exporter/defaults/main.yml`
- Create: `ansible/roles/node-exporter/templates/node_exporter.service.j2`

Static binary install (works uniformly on ubuntu-arm64 and rhel-x86_64), run as a systemd service on `:9100`.

- [ ] **Step 1: Defaults (pinned version + arch map)**

Create `ansible/roles/node-exporter/defaults/main.yml`:

```yaml
---
node_exporter_version: "1.8.2"
node_exporter_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
node_exporter_pkg: "node_exporter-{{ node_exporter_version }}.linux-{{ node_exporter_arch }}"
node_exporter_url: "https://github.com/prometheus/node_exporter/releases/download/v{{ node_exporter_version }}/{{ node_exporter_pkg }}.tar.gz"
```

- [ ] **Step 2: The systemd unit template**

Create `ansible/roles/node-exporter/templates/node_exporter.service.j2`:

```ini
[Unit]
Description=Prometheus node_exporter
After=network-online.target
Wants=network-online.target

[Service]
User=node_exporter
Group=node_exporter
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: The install tasks**

Create `ansible/roles/node-exporter/tasks/main.yml`:

```yaml
---
- name: node_exporter system user
  ansible.builtin.user:
    name: node_exporter
    system: true
    shell: /usr/sbin/nologin
    create_home: false

- name: download + unpack node_exporter
  ansible.builtin.unarchive:
    src: "{{ node_exporter_url }}"
    dest: /tmp
    remote_src: true
    creates: "/tmp/{{ node_exporter_pkg }}/node_exporter"

- name: install the binary
  ansible.builtin.copy:
    src: "/tmp/{{ node_exporter_pkg }}/node_exporter"
    dest: /usr/local/bin/node_exporter
    mode: "0755"
    remote_src: true
  notify: restart node_exporter

- name: install the systemd unit
  ansible.builtin.template:
    src: node_exporter.service.j2
    dest: /etc/systemd/system/node_exporter.service
    mode: "0644"
  notify: restart node_exporter

- name: enable + start node_exporter
  ansible.builtin.systemd:
    name: node_exporter
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 4: The handler**

Create `ansible/roles/node-exporter/handlers/main.yml`:

```yaml
---
- name: restart node_exporter
  ansible.builtin.systemd:
    name: node_exporter
    state: restarted
    daemon_reload: true
```

- [ ] **Step 5: Syntax-check the role wiring (deferred live run to Task 10/11)**

Run: `cd ansible && uv run ansible-playbook --syntax-check -i /dev/null -e 'targets=localhost' /dev/stdin <<'YAML'
- hosts: localhost
  roles: [node-exporter]
YAML`
Expected: `playbook: ...` with no syntax error. (Functional verification happens against live guests in Tasks 10–11.)

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope ansible --message "node-exporter role (#103)" --body "Pinned static node_exporter 1.8.2, arch-detected (arm64/amd64), run as a systemd service on :9100. Applied fleet-wide by ansible/observability.yml (Task 9)."
```

---

### Task 7: `prometheus` Ansible role (on `obs`)

**Files:**
- Create: `ansible/roles/prometheus/tasks/main.yml`
- Create: `ansible/roles/prometheus/defaults/main.yml`
- Create: `ansible/roles/prometheus/templates/prometheus.yml.j2`
- Create: `ansible/roles/prometheus/handlers/main.yml`

Installs the Prometheus server, deploys the topology-rendered `node.json` target file (copied from the host's `build/` tree), and a config that scrapes via `file_sd`.

- [ ] **Step 1: Defaults**

Create `ansible/roles/prometheus/defaults/main.yml`:

```yaml
---
prometheus_version: "2.53.2"
prometheus_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
prometheus_pkg: "prometheus-{{ prometheus_version }}.linux-{{ prometheus_arch }}"
prometheus_url: "https://github.com/prometheus/prometheus/releases/download/v{{ prometheus_version }}/{{ prometheus_pkg }}.tar.gz"
prometheus_retention: "24h"   # lab-ephemeral: short retention, data not precious
```

- [ ] **Step 2: The config template (file_sd, short retention)**

Create `ansible/roles/prometheus/templates/prometheus.yml.j2`:

```yaml
global:
  scrape_interval: 10s
  evaluation_interval: 10s

scrape_configs:
  - job_name: node
    file_sd_configs:
      - files: ["/etc/prometheus/targets/node.json"]
```

- [ ] **Step 3: The install + deploy tasks**

Create `ansible/roles/prometheus/tasks/main.yml`:

```yaml
---
- name: prometheus system user
  ansible.builtin.user:
    name: prometheus
    system: true
    shell: /usr/sbin/nologin
    create_home: false

- name: download + unpack prometheus
  ansible.builtin.unarchive:
    src: "{{ prometheus_url }}"
    dest: /tmp
    remote_src: true
    creates: "/tmp/{{ prometheus_pkg }}/prometheus"

- name: install the prometheus binary
  ansible.builtin.copy:
    src: "/tmp/{{ prometheus_pkg }}/prometheus"
    dest: /usr/local/bin/prometheus
    mode: "0755"
    remote_src: true

- name: config + data + targets dirs
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: prometheus
    group: prometheus
    mode: "0755"
  loop:
    - /etc/prometheus
    - /etc/prometheus/targets
    - /var/lib/prometheus

- name: deploy prometheus.yml
  ansible.builtin.template:
    src: prometheus.yml.j2
    dest: /etc/prometheus/prometheus.yml
    mode: "0644"
  notify: restart prometheus

- name: deploy the topology-rendered node targets
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/prometheus/targets/node.json"
    dest: /etc/prometheus/targets/node.json
    owner: prometheus
    group: prometheus
    mode: "0644"
  notify: restart prometheus

- name: install the systemd unit
  ansible.builtin.copy:
    dest: /etc/systemd/system/prometheus.service
    mode: "0644"
    content: |
      [Unit]
      Description=Prometheus
      After=network-online.target
      Wants=network-online.target

      [Service]
      User=prometheus
      Group=prometheus
      ExecStart=/usr/local/bin/prometheus \
        --config.file=/etc/prometheus/prometheus.yml \
        --storage.tsdb.path=/var/lib/prometheus \
        --storage.tsdb.retention.time={{ prometheus_retention }} \
        --web.listen-address=:9090
      Restart=on-failure

      [Install]
      WantedBy=multi-user.target
  notify: restart prometheus

- name: enable + start prometheus
  ansible.builtin.systemd:
    name: prometheus
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 4: The handler**

Create `ansible/roles/prometheus/handlers/main.yml`:

```yaml
---
- name: restart prometheus
  ansible.builtin.systemd:
    name: prometheus
    state: restarted
    daemon_reload: true
```

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope ansible --message "prometheus role (#103)" --body "Prometheus 2.53.2 on obs: file_sd scraping the topology-rendered node.json (copied from build/), 24h retention, :9090. Targets are rendered by mqlab obs up before provisioning."
```

---

### Task 8: `grafana` Ansible role + fleet dashboard (on `obs`)

**Files:**
- Create: `ansible/roles/grafana/tasks/main.yml`
- Create: `ansible/roles/grafana/defaults/main.yml`
- Create: `ansible/roles/grafana/templates/datasource.yml.j2`
- Create: `ansible/roles/grafana/templates/dashboards.yml.j2`
- Create: `ansible/roles/grafana/files/dashboards/fleet-node.json`
- Create: `ansible/roles/grafana/handlers/main.yml`

Grafana OSS via the official apt repo (obs is ubuntu-arm64), provisioned file-based: the Prometheus datasource + a node-exporter fleet dashboard, so it's reproducible with no click-ops. The admin password is **runtime-injected** (spec §8.2) — never committed.

- [ ] **Step 1: Datasource provisioning template**

Create `ansible/roles/grafana/templates/datasource.yml.j2`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
```

- [ ] **Step 2: Dashboard provider template**

Create `ansible/roles/grafana/templates/dashboards.yml.j2`:

```yaml
apiVersion: 1
providers:
  - name: lab
    folder: Lab
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 3: The fleet dashboard JSON**

Create `ansible/roles/grafana/files/dashboards/fleet-node.json`. Use a minimal, valid Grafana dashboard with two panels — a per-host "up" stat and a CPU-busy timeseries — driven by the `host` label the renderer attaches:

```json
{
  "title": "Lab Fleet — Node Health",
  "uid": "lab-fleet-node",
  "schemaVersion": 39,
  "version": 1,
  "time": { "from": "now-15m", "to": "now" },
  "refresh": "10s",
  "panels": [
    {
      "type": "stat",
      "title": "Node up",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "fieldConfig": { "defaults": { "mappings": [
        { "type": "value", "options": { "0": { "text": "DOWN", "color": "red" }, "1": { "text": "UP", "color": "green" } } }
      ] } },
      "targets": [
        { "expr": "up{job=\"node\"}", "legendFormat": "{{host}}" }
      ]
    },
    {
      "type": "timeseries",
      "title": "CPU busy % (per host)",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "targets": [
        {
          "expr": "100 - (avg by (host) (rate(node_cpu_seconds_total{mode=\"idle\"}[1m])) * 100)",
          "legendFormat": "{{host}}"
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: The install + provision tasks**

Create `ansible/roles/grafana/tasks/main.yml`:

```yaml
---
- name: grafana apt key
  ansible.builtin.get_url:
    url: https://apt.grafana.com/gpg.key
    dest: /etc/apt/keyrings/grafana.asc
    mode: "0644"

- name: grafana apt repo
  ansible.builtin.apt_repository:
    repo: "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://apt.grafana.com stable main"
    filename: grafana
    state: present

- name: install grafana
  ansible.builtin.apt:
    name: grafana
    update_cache: true

- name: provisioning dirs
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: grafana
    group: grafana
    mode: "0755"
  loop:
    - /etc/grafana/provisioning/datasources
    - /etc/grafana/provisioning/dashboards
    - /var/lib/grafana/dashboards

- name: provision the prometheus datasource
  ansible.builtin.template:
    src: datasource.yml.j2
    dest: /etc/grafana/provisioning/datasources/prometheus.yml
    mode: "0644"
  notify: restart grafana

- name: provision the dashboard provider
  ansible.builtin.template:
    src: dashboards.yml.j2
    dest: /etc/grafana/provisioning/dashboards/lab.yml
    mode: "0644"
  notify: restart grafana

- name: deploy the fleet dashboard
  ansible.builtin.copy:
    src: dashboards/fleet-node.json
    dest: /var/lib/grafana/dashboards/fleet-node.json
    owner: grafana
    group: grafana
    mode: "0644"
  notify: restart grafana

- name: enable + start grafana
  ansible.builtin.systemd:
    name: grafana-server
    enabled: true
    state: started
```

- [ ] **Step 5: Inject the admin password from `lab-secret.sh` (spec §8.2)**

Create `ansible/roles/grafana/defaults/main.yml` — the password comes from the
environment (`lab-secret.sh` exports it before the playbook runs), defaulting to
`admin` only when unset so the spike still works:

```yaml
---
grafana_admin_password: "{{ lookup('ansible.builtin.env', 'GF_SECURITY_ADMIN_PASSWORD') | default('admin', true) }}"
```

Add this task to `ansible/roles/grafana/tasks/main.yml` **before** the
"enable + start grafana" task, so the first start already carries the override:

```yaml
- name: inject the admin password via a systemd env drop-in (never committed)
  ansible.builtin.copy:
    dest: /etc/systemd/system/grafana-server.service.d/admin.conf
    mode: "0600"
    content: |
      [Service]
      Environment=GF_SECURITY_ADMIN_PASSWORD={{ grafana_admin_password }}
  notify: restart grafana
```

The drop-in path's parent dir is created by systemd's package install; if the
task fails on a missing dir, add a preceding `ansible.builtin.file` task creating
`/etc/systemd/system/grafana-server.service.d/` with `state: directory`.

- [ ] **Step 6: The handler**

Create `ansible/roles/grafana/handlers/main.yml`:

```yaml
---
- name: restart grafana
  ansible.builtin.systemd:
    name: grafana-server
    state: restarted
    daemon_reload: true
```

- [ ] **Step 7: Commit**

```bash
vrg-commit --type feat --scope ansible --message "grafana role + fleet dashboard (#103)" --body "Grafana OSS via apt on obs, file-provisioned: Prometheus datasource + a node-exporter fleet dashboard (per-host up + CPU busy). Admin password runtime-injected from lab-secret (GF_SECURITY_ADMIN_PASSWORD), never committed (spec 8.2). Reproducible, no click-ops."
```

---

### Task 9: The `site-obs.yml` and `observability.yml` playbooks

**Files:**
- Create: `ansible/site-obs.yml`
- Create: `ansible/observability.yml`

`site-obs.yml` provisions the monitoring pair (obs = node-exporter + prometheus + grafana; mon-probe = node-exporter). `observability.yml` is the fleet-wide overlay that puts `node-exporter` on every running node (Layer 1).

- [ ] **Step 1: `site-obs.yml`**

Create `ansible/site-obs.yml`:

```yaml
# Observability pair (#103). Run via `mqlab obs up`, or directly:
#   uv run ansible-playbook site-obs.yml
- hosts: obs:probe
  become: true
  roles: [node-exporter]

- hosts: obs
  become: true
  roles: [prometheus, grafana]
```

- [ ] **Step 2: `observability.yml` (fleet overlay)**

Create `ansible/observability.yml`:

```yaml
# Fleet-wide host metrics (#103, Layer 1). Run against whatever arm is up, e.g.:
#   uv run ansible-playbook observability.yml --limit rdqm_a
- hosts: all
  become: true
  roles: [node-exporter]
```

- [ ] **Step 3: Syntax-check both playbooks**

Run: `cd ansible && uv run ansible-playbook --syntax-check site-obs.yml && uv run ansible-playbook --syntax-check observability.yml`
Expected: both print `playbook: ...` with no error. (No live hosts needed for a syntax check.)

- [ ] **Step 4: Commit**

```bash
vrg-commit --type feat --scope ansible --message "site-obs + observability playbooks (#103)" --body "site-obs.yml provisions the monitoring pair (obs: node-exporter+prometheus+grafana; mon-probe: node-exporter). observability.yml overlays node-exporter on hosts: all for the fleet-wide Layer-1 rollout."
```

---

### Task 10: Layer-0 spike — bring up `obs` + `mon-probe`, prove the URL opens

**Files:** none (live validation). This is the spike gate: the observability stack stands up and is reachable before we roll node_exporter across the fleet.

- [ ] **Step 1: Ensure the mgmt network and the monitoring VMs exist**

Run:
```bash
mqlab net up net-mgmt
mqlab obs targets        # render build/prometheus/targets/node.json
mqlab vm inventory       # render build/inventory.ini (so site-obs.yml resolves)
mqlab obs up             # render targets + vagrant up obs mon-probe + provision
```
Expected: `obs` and `mon-probe` reach `running`; `site-obs.yml` completes with no failed tasks. If `vagrant up` or an `apt`/`get_url` task fails for **no internet**, stop — this is the assumption check; pivot to pre-staging binaries under `build/` and note it on the issue.

- [ ] **Step 2: Confirm Prometheus is up and scraping obs/mon-probe**

Run: `mqlab vm ssh obs` then on the guest:
```bash
curl -s localhost:9090/-/ready
curl -s 'localhost:9090/api/v1/query?query=up{job="node"}' | python3 -m json.tool
```
Expected: `Prometheus Server is Ready.`; the query returns `up` series for `obs` and `mon-probe` (value `"1"`), proving node_exporter on both is scraped over net-mgmt.

- [ ] **Step 3: Confirm Grafana is up and provisioned**

On the obs guest:
```bash
curl -s localhost:3000/api/health
```
Expected: JSON `{"database": "ok", ...}`.

- [ ] **Step 4: Prove the URL opens from the workstation**

Run `mqlab obs open`, follow the printed tunnel one-liner from your Mac, and load `http://10.50.0.2:3000` (or the tunneled `localhost:3000`). Log in as `admin` with the injected `GF_SECURITY_ADMIN_PASSWORD` (or `admin` if unset for the spike), confirm the **Lab / Fleet — Node Health** dashboard renders with `obs` and `mon-probe` tiles green.
Expected: dashboard loads; both tiles UP. **This is the Layer-0 success criterion — an observability stack you can open.**

- [ ] **Step 5: Commit the spike evidence**

Capture the outcome in a short report and commit:
```bash
vrg-commit --type docs --scope obs --message "Layer-0 spike: obs stack up, URL reachable (#103)" --body "obs+mon-probe provisioned; Prometheus scrapes node_exporter on both over net-mgmt; Grafana health ok; fleet dashboard reachable from the workstation. Records the internet-during-provisioning assumption as confirmed." --allow-empty
```

---

### Task 11: Layer-1 — node_exporter fleet-wide, watch a node drop

**Files:** none (live validation), plus a docs note.

- [ ] **Step 1: Roll node_exporter across a running arm**

Targets are rendered from the **full topology** (Task 2), so the arm's nodes are
*already* Prometheus targets (showing `up == 0` until reachable) — no re-render or
Prometheus restart is needed. The only action is installing node_exporter on the
arm. Bring up an arm and run the fleet overlay:
```bash
mqlab vm up rdqm_ha          # or whichever arm is convenient
cd ansible && uv run ansible-playbook observability.yml --limit rdqm_a
```
Expected: `observability.yml` completes; node_exporter active on `rdqm-a1..a3`; their tiles flip from DOWN to UP on the dashboard as the exporter starts.

- [ ] **Step 2: Confirm the arm appears on the fleet dashboard**

In Grafana, reload **Fleet — Node Health**. Expected: `rdqm-a1/a2/a3` tiles appear and are UP; CPU panel shows their series.

- [ ] **Step 3: Watch a node drop**

Run: `mqlab vm down rdqm-a2` (graceful) or `mqlab vm destroy rdqm-a2` (hard). Watch the dashboard.
Expected: within ~10–20 s the `rdqm-a2` tile flips to DOWN (`up == 0`) — a fault is *visible*, fail-loud, exactly the Layer-1 payoff.

- [ ] **Step 4: Document the access + rollout flow**

Add a short "Observability" subsection to `docs/site/docs/getting-started.md` documenting: `mqlab obs up`, `mqlab obs open`, and `ansible-playbook observability.yml --limit <group>` to add an arm. Then commit:
```bash
vrg-commit --type docs --scope obs --message "document the observability bring-up flow (#103)" --body "getting-started: mqlab obs up/open and the observability.yml fleet overlay; watching a node drop on the fleet dashboard."
```

---

### Task 12: Full validation + finalize

**Files:** none.

- [ ] **Step 1: Run the only validation command**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS — ruff/format/mypy clean, pytest green, 100% branch coverage on the new pure modules (`scrape.py` and the obs CLI commands are fully exercised by `tests/test_scrape.py` + `tests/test_cli_obs.py`). If coverage on `scrape.py`/`cli.py` obs paths is < 100%, add the missing-branch test before proceeding.

- [ ] **Step 2: Confirm the layer is complete against the spec**

Re-read `docs/specs/2026-06-10-lab-observability-design.md` §10 success criteria 1, 2, and 5; confirm 1 (URL opens), 2 (node drop turns a tile red), and 5 (mgmt plane is the scrape path, untouched by drills) are met by Tasks 10–11. Criteria 3–4 are Plans B and C.

- [ ] **Step 3: Open the PR into develop**

```bash
vrg-gh pr create --base develop --title "feat(obs): observability foundation + fleet infra (Layers 0-1) (#103)" --body-file <path-to-PR-body>
```
PR body summarizes: the `obs`/`mon-probe` nodes + `monitoring` setup, the topology-rendered scrape targets, the `mqlab obs` slice, the three Ansible roles, the fleet dashboard, and the Layer-0/1 live evidence. Note that Plans B (MQ + cluster health) and C (DR-state view) follow.

---

## Self-review notes

- **Spec coverage:** §3.1 (obs/mon-probe nodes) → Task 1; §3.2 (consume net-mgmt) → Tasks 1,10; §3.3 node_exporter row → Tasks 6,9,11; §3.6 (render targets from topology, fail-loud) → Tasks 2,3; §4 Layer 0 → Task 10; §4 Layer 1 → Task 11; §5 (URL access) → Tasks 5,10; §6 (`mqlab obs` slice) → Tasks 4,5; §7 (provisioned as code) → Tasks 7,8,9; §8.1 (fail-loud `up==0`) → Task 11; §8.3 (pytest + real-topology guard + vrg-validate) → Tasks 3,12. MQ rows (§3.3 mq/ha_cluster), §3.4, §3.5, Layers 2–3 are explicitly deferred to Plans B/C.
- **Type consistency:** `render_scrape_targets(topo)`, `scrape_targets_path()`, `lab_scrape_targets()`, `ScrapeError`, `_obs_up_steps()`, `GRAFANA_URL` are used with identical names/signatures across Tasks 2, 4, 5, 7. Renderer port constant `NODE_EXPORTER_PORT = 9100` matches the dashboard's `job="node"` and the prometheus.yml `job_name: node`.
- **Orchestrator fit:** confirmed `CommandStep(label, command)` is a frozen two-field dataclass (no `pre` hook) and `Command(argv, cwd=None)` — Task 5 renders targets eagerly when building the steps, needing no orchestrator change. No deferred-to-codebase guesses remain.
