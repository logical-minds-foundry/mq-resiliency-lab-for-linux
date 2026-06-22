# Lab Log Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-tail the two simulator apps' messages (and, later, any node log) into the existing Grafana dashboard by adding a Loki + Grafana Alloy pipeline.

**Architecture:** Both sim apps print generic syslog-shaped JSON (`ts`/`level`/`msg`) to stdout via a tiny shared emitter; they are launched under a stable `systemd-run` unit so journald tags each line with `unit=mqlab-*`. A fleet-wide Grafana Alloy role scrapes journald (low-cardinality labels `host` + `unit`) and ships to a single-binary Loki on the `obs` VM. The dashboard renderer gains a curated log-source catalog that emits Grafana Logs panels in a new top-of-dashboard "Application messages" row.

**Tech Stack:** Python 3.12 (Typer CLI, pure-function renderers), Ansible roles (mirror `node-exporter`/`prometheus`/`grafana`), Grafana Loki 3.x, Grafana Alloy 1.x, systemd/journald, pytest + ruff + mypy under `vrg-validate`.

**Conventions for every task:**
- This is a Vergil-managed repo. Work inside the worktree `.worktrees/issue-143-log-streaming/` on branch `feature/143-log-streaming`. Use `vrg-git` / `vrg-commit`, never raw `git`.
- Validation is **only** `vrg-container-run -- vrg-validate` (runs ruff, mypy strict, pytest with branch coverage). Don't run individual linters. New Python must reach 100% branch coverage (see repo memory "validation gotchas").
- Ansible roles cannot be pytest-verified; their correctness is proven by the human operating the lab (Task 11). Python + lint is what `vrg-validate` gates.

**Reference reading (don't re-derive these):**
- `ansible/roles/node-exporter/` — the binary+systemd+role pattern to mirror for `alloy`.
- `ansible/roles/prometheus/` — the binary+config+retention pattern to mirror for `loki`.
- `ansible/roles/grafana/templates/datasource.yml.j2` — where the Loki datasource is added.
- `src/mqlab/dashboard.py` — the pure renderer to extend (`ROWS`, `NET_SECTIONS`, `render_dashboard`).
- `clients/epn_requester.py`, `clients/epn_responder.py` — the apps to convert.
- `lab/scripts/e2e-test.sh` — the launch harness to wrap in `systemd-run`.

**The unit-name contract (used in three places, keep identical):**
- `mqlab-requester` — app-side requester (`epn_requester.py`, runs on `app-client`).
- `mqlab-responder` — SVC-side responder (`epn_responder.py`, runs on `svc-sim`).
These exact strings appear in `e2e-test.sh` (the `systemd-run --unit=` flag) and in `dashboard.py` (`LOG_SOURCES` selectors). If you change one, change all.

---

## Task 1: The structured emitter (`mqlab/obslog.py`)

A tiny, dependency-free, generic emitter. One JSON object per line to stdout: `{"ts","level","msg"}`. `host`/`unit` are added downstream by journald+Alloy, never here. Shipped flat next to the client scripts (deployed as `/home/vagrant/obslog.py`, imported as `from obslog import emit`); importable in tests as `mqlab.obslog`.

**Files:**
- Create: `src/mqlab/obslog.py`
- Test: `tests/test_obslog.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_obslog.py
from __future__ import annotations

import json
from datetime import UTC, datetime

from mqlab.obslog import _format_ts, emit, format_line


def test_format_line_is_one_json_object_with_exactly_ts_level_msg():
    line = format_line("info", "hello world", "2026-06-12T14:03:01.123Z")
    obj = json.loads(line)
    assert obj == {"ts": "2026-06-12T14:03:01.123Z", "level": "info", "msg": "hello world"}
    assert "\n" not in line  # the newline is added by emit's print, not the record


def test_format_ts_is_rfc3339_utc_with_millis_and_z():
    ts = _format_ts(datetime(2026, 6, 12, 14, 3, 1, 123456, tzinfo=UTC))
    assert ts == "2026-06-12T14:03:01.123Z"


def test_emit_writes_a_single_json_line_to_an_injected_stream():
    import io

    buf = io.StringIO()
    fixed = datetime(2026, 6, 12, 14, 3, 1, 0, tzinfo=UTC)
    emit("warn", "ack 7: 0001", stream=buf, clock=lambda: fixed)
    out = buf.getvalue()
    assert out.endswith("\n")
    assert out.count("\n") == 1
    obj = json.loads(out)
    assert obj["level"] == "warn"
    assert obj["msg"] == "ack 7: 0001"
    assert obj["ts"] == "2026-06-12T14:03:01.000Z"


def test_emit_defaults_to_stdout_and_real_clock(capsys):
    emit("info", "default path")
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    obj = json.loads(out)
    assert obj["level"] == "info"
    assert obj["msg"] == "default path"
    # real clock -> RFC3339 UTC string ending in Z
    assert obj["ts"].endswith("Z")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `vrg-container-run -- uv run pytest tests/test_obslog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.obslog'`.

- [ ] **Step 3: Write the implementation**

```python
# src/mqlab/obslog.py
"""Generic, syslog-shaped structured log emitter for the lab's observation apps.

One JSON object per line to stdout: {"ts", "level", "msg"}. host + unit are added
downstream as journald + Alloy labels, never in the line. Deliberately tiny and
dependency-free so it ships flat next to the client scripts (deployed as
/home/vagrant/obslog.py, imported as `from obslog import emit`) and still imports
as mqlab.obslog in tests.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO


def format_line(level: str, msg: str, ts: str) -> str:
    """Pure: build the one-line JSON record (no trailing newline)."""
    return json.dumps({"ts": ts, "level": level, "msg": msg}, separators=(",", ":"))


def _format_ts(now: datetime) -> str:
    """RFC 3339, UTC, millisecond precision, Z suffix."""
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(
    level: str,
    msg: str,
    *,
    stream: TextIO | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Print one JSON log line to stdout. stream + clock are injectable for tests.

    flush=True so lines reach journald promptly for live tail.
    """
    out = sys.stdout if stream is None else stream
    now = datetime.now(tz=UTC) if clock is None else clock()
    print(format_line(level, msg, _format_ts(now)), file=out, flush=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `vrg-container-run -- uv run pytest tests/test_obslog.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS — ruff/mypy clean, full suite green, `obslog.py` at 100% branch coverage (the four tests cover both `stream`/`clock` branches).

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/obslog.py tests/test_obslog.py
vrg-commit --type feat --scope obs --message "add generic syslog-shaped log emitter (obslog) for #143" --body "One JSON object per line (ts/level/msg) to stdout; host/unit are journald+Alloy labels downstream. Injectable stream+clock for tests. Shipped flat next to the client scripts."
```

---

## Task 2: Dashboard log-source catalog + "Application messages" row

Add a curated catalog rendering Grafana Logs panels, in a new row at the **very top** (above the MQ Service row). Hierarchy top→bottom becomes: Application messages → MQ service → VMs → networks.

**Files:**
- Modify: `src/mqlab/dashboard.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Update + add the failing tests**

In `tests/test_dashboard.py`, change the expected row order in `test_has_a_row_header_per_curated_row_in_order` to prepend the new row:

```python
def test_has_a_row_header_per_curated_row_in_order():
    panels = render_dashboard(TOPO)["panels"]
    row_titles = [p["title"] for p in panels if p["type"] == "row"]
    assert row_titles == [
        "Application messages",
        "MQ Service — reserved · Layer 2",
        "VMs · PCMK · A",
        "VMs · PCMK · B",
        "VMs · RDQM · A",
        "VMs · RDQM · B",
        "VMs · Standalone",
        "VMs · Observability",
        "Networks · Message path",
        "Networks · Cluster + storage",
        "Networks · Cross-site + mgmt",
    ]
```

Then append two new tests:

```python
def test_application_messages_row_is_first():
    panels = render_dashboard(TOPO)["panels"]
    first_row = next(p for p in panels if p["type"] == "row")
    assert first_row["title"] == "Application messages"
    assert first_row["gridPos"]["y"] == 0


def test_log_panels_query_loki_for_the_two_sim_app_units():
    panels = render_dashboard(TOPO)["panels"]
    logs = [p for p in panels if p["type"] == "logs"]
    assert [p["title"] for p in logs] == ["App app — requester", "SVC app — responder"]
    for p in logs:
        assert p["datasource"] == {"type": "loki", "uid": "loki"}
        target = p["targets"][0]
        assert target["datasource"] == {"type": "loki", "uid": "loki"}
        assert target["expr"].endswith("| json")
    assert logs[0]["targets"][0]["expr"] == '{unit="mqlab-requester"} | json'
    assert logs[1]["targets"][0]["expr"] == '{unit="mqlab-responder"} | json'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `vrg-container-run -- uv run pytest tests/test_dashboard.py -v`
Expected: FAIL — `test_application_messages_row_is_first` / `test_log_panels_query_loki...` (no `logs` panels yet) and the reordered `row_titles` assertion.

- [ ] **Step 3: Add the catalog + panel builder to `dashboard.py`**

Add after the `DASHBOARD_UID` line (around line 23):

```python
# The provisioned Loki datasource (uid pinned in grafana/datasource.yml.j2 so
# panels can reference it deterministically, the way the dashboard uid is pinned).
LOKI_DS = {"type": "loki", "uid": "loki"}

# Curated log sources -> live Logs panels in the top "Application messages" row.
# The selector keys off the systemd unit the launch wrapper uses (mqlab-*);
# adding a source later (cluster daemons, AMQERR) is one more entry here.
LOG_SOURCES: list[tuple[str, str]] = [
    ("App app — requester", '{unit="mqlab-requester"}'),
    ("SVC app — responder", '{unit="mqlab-responder"}'),
]
```

Add this panel builder near the other `_*_panel` helpers (e.g. after `_text`):

```python
def _logs_panel(title: str, selector: str, y: int) -> dict[str, Any]:
    # `| json` parses our ts/level/msg fields; the panel displays msg and colors
    # by level. The dashboard's 10s refresh + descending sort gives the live feel;
    # true websocket tail is via Grafana Explore (see `mqlab obs open`).
    return {
        "type": "logs",
        "title": title,
        "datasource": LOKI_DS,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
        "targets": [{"datasource": LOKI_DS, "expr": f"{selector} | json"}],
    }
```

In `render_dashboard`, insert the new row **before** the existing MQ Service row. Change this opening block:

```python
    known = set(topo.get("groups", {}))
    panels: list[dict[str, Any]] = []
    y = 0

    panels.append(_row("MQ Service — reserved · Layer 2", y))
```

to:

```python
    known = set(topo.get("groups", {}))
    panels: list[dict[str, Any]] = []
    y = 0

    panels.append(_row("Application messages", y))
    y += 1
    for title, selector in LOG_SOURCES:
        panels.append(_logs_panel(title, selector, y))
        y += 8

    panels.append(_row("MQ Service — reserved · Layer 2", y))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `vrg-container-run -- uv run pytest tests/test_dashboard.py -v`
Expected: PASS (all dashboard tests green).

- [ ] **Step 5: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS — new lines covered by the render tests (every `render_dashboard` test exercises the catalog loop and `_logs_panel`).

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/dashboard.py tests/test_dashboard.py
vrg-commit --type feat --scope obs --message "render Loki Logs panels in a top 'Application messages' row for #143" --body "Curated LOG_SOURCES catalog -> Grafana logs panels referencing the pinned Loki datasource (uid=loki). New row at the very top; hierarchy is now application -> MQ service -> VMs -> networks."
```

---

## Task 3: Loki Ansible role (single-binary, on `obs`)

Mirror the `prometheus` role: download the binary, deploy config + systemd unit, 24h retention.

**Files:**
- Create: `ansible/roles/loki/defaults/main.yml`
- Create: `ansible/roles/loki/handlers/main.yml`
- Create: `ansible/roles/loki/templates/loki-config.yml.j2`
- Create: `ansible/roles/loki/tasks/main.yml`

- [ ] **Step 1: `defaults/main.yml`**

```yaml
---
loki_version: "3.1.1"  # confirm current stable in Task 11; URL must resolve
loki_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
loki_pkg: "loki-linux-{{ loki_arch }}"
loki_url: "https://github.com/grafana/loki/releases/download/v{{ loki_version }}/{{ loki_pkg }}.zip"
loki_retention: "24h"   # lab-ephemeral, mirrors prometheus_retention
```

- [ ] **Step 2: `handlers/main.yml`**

```yaml
---
- name: restart loki
  ansible.builtin.systemd:
    name: loki
    state: restarted
    daemon_reload: true
```

- [ ] **Step 3: `templates/loki-config.yml.j2`**

```yaml
# Single-binary Loki for the ephemeral lab: filesystem storage, short retention.
# Listens on :3100 (all interfaces) so Alloy on the other nodes can push over
# the mgmt plane. Grafana (co-located on obs) queries http://localhost:3100.
auth_enabled: false

server:
  http_listen_port: 3100
  grpc_listen_port: 9096
  log_level: warn

common:
  instance_addr: 127.0.0.1
  path_prefix: /var/lib/loki
  storage:
    filesystem:
      chunks_directory: /var/lib/loki/chunks
      rules_directory: /var/lib/loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

limits_config:
  retention_period: {{ loki_retention }}
  reject_old_samples: false
  allow_structured_metadata: true
  volume_enabled: true

compactor:
  working_directory: /var/lib/loki/compactor
  retention_enabled: true
  delete_request_store: filesystem

analytics:
  reporting_enabled: false
```

- [ ] **Step 4: `tasks/main.yml`**

```yaml
---
- name: loki system user
  ansible.builtin.user:
    name: loki
    system: true
    shell: /usr/sbin/nologin
    create_home: false

- name: unzip (needed to unpack the loki release archive)
  ansible.builtin.apt:
    name: unzip
    update_cache: true

- name: download + unpack loki
  ansible.builtin.unarchive:
    src: "{{ loki_url }}"
    dest: /tmp
    remote_src: true
    creates: "/tmp/{{ loki_pkg }}"

- name: install the loki binary
  ansible.builtin.copy:
    src: "/tmp/{{ loki_pkg }}"
    dest: /usr/local/bin/loki
    mode: "0755"
    remote_src: true

- name: loki data dirs
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    owner: loki
    group: loki
    mode: "0755"
  loop:
    - /etc/loki
    - /var/lib/loki
    - /var/lib/loki/chunks
    - /var/lib/loki/compactor

- name: deploy loki config
  ansible.builtin.template:
    src: loki-config.yml.j2
    dest: /etc/loki/config.yml
    owner: loki
    group: loki
    mode: "0644"
  notify: restart loki

- name: install the systemd unit
  ansible.builtin.copy:
    dest: /etc/systemd/system/loki.service
    mode: "0644"
    content: |
      [Unit]
      Description=Grafana Loki
      After=network-online.target
      Wants=network-online.target

      [Service]
      User=loki
      Group=loki
      ExecStart=/usr/local/bin/loki -config.file=/etc/loki/config.yml
      Restart=on-failure

      [Install]
      WantedBy=multi-user.target
  notify: restart loki

- name: enable + start loki
  ansible.builtin.systemd:
    name: loki
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 5: Validate (lint only — no live run here)**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (no Python change; confirms nothing else broke). Live verification is Task 11.

- [ ] **Step 6: Commit**

```bash
vrg-git add ansible/roles/loki/
vrg-commit --type feat --scope obs --message "add single-binary Loki role (24h retention) for #143" --body "Mirrors the prometheus role: download binary, filesystem storage, :3100 on the mgmt plane, compactor-based retention. Wired into site-obs.yml in a later task."
```

---

## Task 4: Provision the Loki datasource in Grafana

Add a second, uid-pinned datasource so the Logs panels resolve deterministically.

**Files:**
- Modify: `ansible/roles/grafana/templates/datasource.yml.j2`

- [ ] **Step 1: Append the Loki datasource**

Replace the file contents with:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
  - name: Loki
    type: loki
    uid: loki          # pinned — dashboard Logs panels reference {type: loki, uid: loki}
    access: proxy
    url: http://localhost:3100
```

- [ ] **Step 2: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/grafana/templates/datasource.yml.j2
vrg-commit --type feat --scope obs --message "provision the Loki datasource (uid=loki) in Grafana for #143"
```

---

## Task 5: Grafana Alloy Ansible role (fleet-wide journald → Loki)

Mirror the `node-exporter` role. Alloy reads the system journal, attaches low-cardinality labels (`host`, `unit`), and pushes to Loki on `obs`.

**Files:**
- Create: `ansible/roles/alloy/defaults/main.yml`
- Create: `ansible/roles/alloy/handlers/main.yml`
- Create: `ansible/roles/alloy/templates/config.alloy.j2`
- Create: `ansible/roles/alloy/tasks/main.yml`

- [ ] **Step 1: `defaults/main.yml`**

```yaml
---
alloy_version: "1.3.1"  # confirm current stable in Task 11; URL must resolve
alloy_arch: "{{ 'arm64' if ansible_architecture == 'aarch64' else 'amd64' }}"
alloy_pkg: "alloy-linux-{{ alloy_arch }}"
alloy_url: "https://github.com/grafana/alloy/releases/download/v{{ alloy_version }}/{{ alloy_pkg }}.zip"
loki_push_url: "http://10.50.0.2:3100/loki/api/v1/push"   # obs net-mgmt IP : Loki
```

- [ ] **Step 2: `handlers/main.yml`**

```yaml
---
- name: restart alloy
  ansible.builtin.systemd:
    name: alloy
    state: restarted
    daemon_reload: true
```

- [ ] **Step 3: `templates/config.alloy.j2`**

```hcl
// Grafana Alloy: ship the systemd journal to Loki on obs.
// Labels stay low-cardinality (host + unit); the JSON payload is parsed in
// Grafana with `| json`. host is templated from the Ansible inventory name.

loki.relabel "journal" {
  forward_to = []

  rule {
    source_labels = ["__journal__systemd_unit"]
    target_label  = "unit"
  }
}

loki.source.journal "system" {
  forward_to    = [loki.write.obs.receiver]
  relabel_rules = loki.relabel.journal.rules
  labels        = {
    host = "{{ inventory_hostname }}",
  }
}

loki.write "obs" {
  endpoint {
    url = "{{ loki_push_url }}"
  }
}
```

- [ ] **Step 4: `tasks/main.yml`**

```yaml
---
- name: unzip (needed to unpack the alloy release archive)
  ansible.builtin.apt:
    name: unzip
    update_cache: true

- name: download + unpack alloy
  ansible.builtin.unarchive:
    src: "{{ alloy_url }}"
    dest: /tmp
    remote_src: true
    creates: "/tmp/{{ alloy_pkg }}"

- name: install the alloy binary
  ansible.builtin.copy:
    src: "/tmp/{{ alloy_pkg }}"
    dest: /usr/local/bin/alloy
    mode: "0755"
    remote_src: true

- name: alloy config + data dirs
  ansible.builtin.file:
    path: "{{ item }}"
    state: directory
    mode: "0755"
  loop:
    - /etc/alloy
    - /var/lib/alloy

- name: deploy alloy config (journald -> loki)
  ansible.builtin.template:
    src: config.alloy.j2
    dest: /etc/alloy/config.alloy
    mode: "0644"
  notify: restart alloy

- name: install the systemd unit
  ansible.builtin.copy:
    dest: /etc/systemd/system/alloy.service
    mode: "0644"
    content: |
      [Unit]
      Description=Grafana Alloy (journald -> Loki)
      After=network-online.target
      Wants=network-online.target

      [Service]
      # root: read the system journal without extra group wiring. Acceptable in
      # an ephemeral, mgmt-plane-only lab.
      User=root
      ExecStart=/usr/local/bin/alloy run --storage.path=/var/lib/alloy /etc/alloy/config.alloy
      Restart=on-failure

      [Install]
      WantedBy=multi-user.target
  notify: restart alloy

- name: enable + start alloy
  ansible.builtin.systemd:
    name: alloy
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 5: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add ansible/roles/alloy/
vrg-commit --type feat --scope obs --message "add Grafana Alloy role (journald -> Loki) for #143" --body "Mirrors node-exporter: download binary, systemd unit, templated config. Scrapes the system journal, labels host+unit only, pushes to Loki on obs. Wired into observability.yml + site-obs.yml in a later task."
```

---

## Task 6: Wire Loki + Alloy into the playbooks

Loki runs on `obs`; Alloy runs everywhere telemetry does.

**Files:**
- Modify: `ansible/site-obs.yml`
- Modify: `ansible/observability.yml`

- [ ] **Step 1: Add `loki` + `alloy` to `site-obs.yml`**

Replace the file with:

```yaml
# Observability pair (#103). Run via `mqlab obs up`, or directly:
#   uv run ansible-playbook site-obs.yml
- hosts: obs_box:probe
  become: true
  roles: [node-exporter, alloy]

- hosts: obs_box
  become: true
  roles: [prometheus, grafana, loki]
```

(Alloy on `obs` ships before Loki starts in the second play; Alloy retries pushes until Loki is up — acceptable.)

- [ ] **Step 2: Add `alloy` to `observability.yml`**

Replace the `roles:` list so fleet instrumentation installs Alloy alongside node_exporter:

```yaml
  roles:
    - node-exporter
    - alloy
    - {role: net-reach, when: reach_peers | length > 0}
```

(Leave the `vars:` block and header comment unchanged.)

- [ ] **Step 3: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS. Confirm the CLI obs tests still pass (they assert `site-obs.yml` / `observability.yml` invocation, not role contents):
`vrg-container-run -- uv run pytest tests/test_cli_obs.py -v`

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/site-obs.yml ansible/observability.yml
vrg-commit --type feat --scope obs --message "wire Loki (obs) + Alloy (fleet) into the obs playbooks for #143"
```

---

## Task 7: Deploy `obslog.py` and convert the two sim apps

Ship the emitter to the nodes and make both apps emit through it. MQ logic is unchanged — only logging is added/replaced.

**Files:**
- Modify: `ansible/site.yml` (the "deploy FFH codec + clients" loop)
- Modify: `clients/epn_requester.py`
- Modify: `clients/epn_responder.py`

- [ ] **Step 1: Ship `obslog.py` next to the clients**

In `ansible/site.yml`, add one line to the deploy loop (after the `header.py` entry, around line 42):

```yaml
        - { src: "{{ playbook_dir }}/../src/mqlab/obslog.py", dest: obslog.py }
```

- [ ] **Step 2: Convert `epn_requester.py` to emit**

Add the import (with the other imports):

```python
from obslog import emit
```

Replace the per-ack `print` and the summary `print`, and add a connect line. The loop body changes from:

```python
    for i in range(count):
        body = pack_header(
            password="pw", sender="APP01", receiver="SVC", session_date=today
        ) + f"MSG-{i:04d}"
        qreq.put(body.encode())
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=60_000)
    acks = 0
    for _ in range(count):
        reply = qrep.get(None, pymqi.MD(), gmo).decode()
        code, seq = parse_header(reply).payload.split(":", 1)
        print(f"ack {seq}: {code}")
        if code == "0000":
            acks += 1
    qmgr.disconnect()
    print(f"{acks}/{count} clean ACKs")
```

to:

```python
    emit("info", f"connected to QMAIN; putting {count} trades to SVC.REQUEST")
    for i in range(count):
        body = pack_header(
            password="pw", sender="APP01", receiver="SVC", session_date=today
        ) + f"MSG-{i:04d}"
        qreq.put(body.encode())
        emit("info", f"sent trade seq={i:04d} to SVC.REQUEST")
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=60_000)
    acks = 0
    for _ in range(count):
        reply = qrep.get(None, pymqi.MD(), gmo).decode()
        code, seq = parse_header(reply).payload.split(":", 1)
        emit("info" if code == "0000" else "warn", f"ack seq={seq} code={code}")
        if code == "0000":
            acks += 1
    qmgr.disconnect()
    emit("info", f"done: {acks}/{count} clean ACKs")
```

- [ ] **Step 3: Convert `epn_responder.py` to emit**

Add the import:

```python
from obslog import emit
```

Replace the body of `main` from the `gmo = ...` line through `qmgr.disconnect()`:

```python
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=30_000)
    for _ in range(count):
        msg = qin.get(None, pymqi.MD(), gmo).decode()
        ack = validate_header(msg, today=today)
        seq = parse_header(msg).payload if ack == ACK_OK else "?"
        reply = pack_header(
            password="pw", sender="SVC", receiver="APP01", session_date=today
        ) + f"{ack}:{seq}"
        qout.put(reply.encode())
    qmgr.disconnect()
    return 0
```

with:

```python
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=30_000)
    emit("info", f"responder up on QMSVC; awaiting {count} trades")
    for _ in range(count):
        msg = qin.get(None, pymqi.MD(), gmo).decode()
        ack = validate_header(msg, today=today)
        seq = parse_header(msg).payload if ack == ACK_OK else "?"
        emit("info" if ack == ACK_OK else "warn", f"recv trade seq={seq} -> ack {ack}")
        reply = pack_header(
            password="pw", sender="SVC", receiver="APP01", session_date=today
        ) + f"{ack}:{seq}"
        qout.put(reply.encode())
    qmgr.disconnect()
    emit("info", f"done: replied to {count} trades")
    return 0
```

- [ ] **Step 4: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS. The `clients/` scripts import `pymqi` / flat `header` / `obslog` that only resolve on the node — the repo's gate already tolerates this for the existing clients (they import `pymqi` and `from header import ...` today). If `vrg-validate` surfaces a *new* ruff/mypy error specifically from these edits, fix it in keeping with the existing file style; do not introduce new ignores.

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/site.yml clients/epn_requester.py clients/epn_responder.py
vrg-commit --type feat --scope obs --message "emit structured logs from the sim apps via obslog for #143" --body "Ship obslog.py next to the clients; requester logs connect/send/ack/summary, responder (previously silent) logs up/recv/summary. MQ logic unchanged."
```

---

## Task 8: Wrap the e2e launches in a stable `systemd-run` unit

So journald tags every app line with `unit=mqlab-*` and Alloy can select it. `--wait` keeps the harness's exit-code semantics; `--collect` makes the unit name reusable across runs.

**Files:**
- Modify: `lab/scripts/e2e-test.sh`

- [ ] **Step 1: Wrap both launch lines**

Change the responder launch line from:

```bash
vagrant ssh svc-sim -c "~/mqvenv/bin/python ~/epn_responder.py $N" &
```

to:

```bash
# systemd-run --unit pins the journald `unit` label (mqlab-responder) for Alloy;
# --wait preserves the exit code; --collect frees the unit name for re-runs.
vagrant ssh svc-sim -c "sudo systemd-run --unit=mqlab-responder --collect --wait ~/mqvenv/bin/python ~/epn_responder.py $N" &
```

Change the requester launch line from:

```bash
vagrant ssh app-client -c "~/mqvenv/bin/python ~/epn_requester.py $N" || RC=$?
```

to:

```bash
vagrant ssh app-client -c "sudo systemd-run --unit=mqlab-requester --collect --wait ~/mqvenv/bin/python ~/epn_requester.py $N" || RC=$?
```

- [ ] **Step 2: Lint the shell script via validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (if `vrg-validate` includes shellcheck, the quoting above is clean — `$N` stays inside the double-quoted remote command as before).

- [ ] **Step 3: Commit**

```bash
vrg-git add lab/scripts/e2e-test.sh
vrg-commit --type feat --scope obs --message "launch sim apps under systemd-run units so journald tags them for Loki (#143)" --body "mqlab-responder / mqlab-requester transient units; --wait preserves the clean-ACK exit code, --collect makes the unit names reusable."
```

---

## Task 9: Add a live-tail hint to `mqlab obs open`

Point the operator at Grafana Explore for true websocket tailing (the dashboard panels refresh on the 10s interval).

**Files:**
- Modify: `src/mqlab/cli.py` (the `obs_open` command, ~line 335)
- Test: `tests/test_cli_obs.py` (`test_obs_open_prints_url_and_tunnel`)

- [ ] **Step 1: Extend the failing test**

In `tests/test_cli_obs.py`, add two assertions to `test_obs_open_prints_url_and_tunnel`:

```python
def test_obs_open_prints_url_and_tunnel():
    result = CliRunner().invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert "http://10.50.0.2:3000" in result.stdout
    assert "-L 3000:10.50.0.2:3000" in result.stdout
    assert "limactl list" in result.stdout
    assert "/explore" in result.stdout
    assert "mqlab-requester" in result.stdout
```

- [ ] **Step 2: Run it to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_cli_obs.py::test_obs_open_prints_url_and_tunnel -v`
Expected: FAIL — `/explore` not in output.

- [ ] **Step 3: Add the hint lines to `obs_open`**

After the existing `Dashboard:` echo line (around line 339), insert:

```python
    typer.echo(f"Live tail: {GRAFANA_URL}/explore  (pick the Loki datasource, e.g.")
    typer.echo('           query {unit="mqlab-requester"} and toggle Live)')
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_cli_obs.py::test_obs_open_prints_url_and_tunnel -v`
Expected: PASS.

- [ ] **Step 5: Full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_obs.py
vrg-commit --type feat --scope obs --message "point `obs open` at Grafana Explore for live log tail (#143)"
```

---

## Task 10: Re-render the build artifact + open the PR

- [ ] **Step 1: Re-render the dashboard JSON (so the committed build matches the new renderer)**

Run: `vrg-container-run -- uv run mqlab obs dashboard`
Then confirm the Application-messages row is present:
Run: `vrg-container-run -- uv run python -c "import json,pathlib; d=json.loads(pathlib.Path('build/grafana/dashboards/lab-status.json').read_text()); print([p['title'] for p in d['panels'] if p['type']=='row'][:1])"`
Expected: `['Application messages']`
(Note: `build/` is gitignored — this is a sanity check, not a commit.)

- [ ] **Step 2: Final full validation**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS — clean ruff/mypy, full suite green, 100% branch coverage.

- [ ] **Step 3: Push and open the PR into `develop`**

```bash
vrg-git push -u origin feature/143-log-streaming
vrg-gh pr create --base develop --title "Live log streaming: sim-app logs into Grafana via Loki + Alloy (#143)" --body "Implements docs/specs/2026-06-12-lab-log-streaming-design.md. Closes #143.

Pipeline: sim apps print syslog-shaped JSON (obslog) -> systemd-run unit -> journald -> Alloy (fleet) -> Loki (obs) -> Grafana Logs panels in a new top 'Application messages' row. Includes Loki + Alloy Ansible roles, the Loki datasource, app conversion, e2e launch wrapping, and an Explore hint in obs open.

Lab verification performed: see Task 11 checklist in the plan."
```

---

## Task 11: Lab verification (human-operated)

Ansible roles and the live pipeline can't be unit-tested; this is the real integration test. Per repo practice the human drives the lab. Hand this checklist to the operator (or run if you have lab access). **Do this before merging.**

- [ ] **Confirm the binary versions resolve.** The `loki_version` (3.1.1) and `alloy_version` (1.3.1) pins in the role defaults must correspond to real release assets. If a download 404s, bump to the current stable `vX.Y.Z` whose `…-linux-<arch>.zip` exists and re-run.
- [ ] **Bring up obs + instrument the standalone setup:**
  ```bash
  uv run mqlab obs up
  uv run mqlab obs instrument standalone
  ```
- [ ] **Verify Loki is healthy on obs:** `curl -s http://10.50.0.2:3100/ready` → `ready`.
- [ ] **Verify Alloy is shipping:** on `app-client`/`svc-sim`, `systemctl status alloy` is active.
- [ ] **Run a test and watch the logs land:**
  ```bash
  ./lab/scripts/e2e-test.sh 5
  ```
  Then in Grafana (`mqlab obs open` → tunnel → dashboard `lab-fleet-node`): the top **Application messages** row shows `mqlab-requester` and `mqlab-responder` lines (connect / sent / ack / done). In **Explore** → Loki → `{unit="mqlab-requester"}` with Live on, lines stream during a run.
- [ ] **Confirm label discipline:** in Explore, the stream has only `host` and `unit` labels (the JSON fields appear via `| json`, not as labels).

---

## Self-Review

**Spec coverage** (against `2026-06-12-lab-log-streaming-design.md`):
- §3 Loki role on obs → Task 3; Loki datasource → Task 4; Alloy role fleet-wide → Tasks 5–6. ✓
- §3 label discipline (host + unit only) → Alloy config Task 5; verified Task 11. ✓
- §4 generic syslog-shaped emitter (ts/level/msg) → Task 1. ✓
- §5 stdout → journald via systemd-run, unit names, e2e-test.sh → Tasks 7–8. ✓
- §6 Alloy journald scrape → Loki on obs → Task 5. ✓
- §7 log-source catalog → Logs panels, "Application messages" row at top, hierarchy → Task 2. ✓
- §8 CLI: `obs open` Explore hint → Task 9; the `obs logs` convenience is the deferred "maybe" (§11) and is intentionally not built. ✓
- §9 testing (renderer tests, emitter tests, vrg-validate) → Tasks 1, 2, 9; Ansible config is Ansible-templated (no Python render fn), verified live in Task 11 — matches §9's "if templated from topology" being a no-op here. ✓
- §10 scope — all "in" items have tasks; deferred items (cluster panel, AMQERR, log metrics) are not built. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the only deliberately-flexible values are the version pins, which carry an explicit Task-11 confirmation step. ✓

**Type/name consistency:** unit names `mqlab-requester` / `mqlab-responder` identical across Task 2 (`LOG_SOURCES`), Task 8 (`--unit=`), Task 9 (hint), Task 11. Loki datasource `{type: loki, uid: loki}` identical across Task 2 (`LOKI_DS`) and Task 4 (`uid: loki`). `emit(level, msg, *, stream, clock)` signature used in Task 1 matches calls in Task 7. `loki_push_url` / `loki_retention` defined in role defaults and referenced in the matching templates. ✓
