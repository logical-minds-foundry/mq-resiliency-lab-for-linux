# MQ JSON Diagnostic Logging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure every IBM MQ diagnostic surface (queue manager, system, client, mqweb) to emit single-line JSON into journald (+ one file for mqweb), and make it queryable in Loki — production/availability only, no dashboards.

**Architecture:** A new OS-agnostic `mq-diag-logging` Ansible role owns the logging contract (mqs.ini/qm.ini/mqclient.ini stanzas via `blockinfile`, a journald rate-limit drop-in). It is *included* at each per-arm QM-creation seam. MQ-core surfaces use the **Syslog** diagnostic service → journald (`SYSLOG_IDENTIFIER=ibm-mq`); mqweb uses Liberty `messageFormat=json` → `messages.log`. Two Alloy additions (a conditional relabel + a guarded file source) make both findable in Loki.

**Tech Stack:** Ansible (roles, `blockinfile`, `include_role`/`tasks_from`), Jinja2 (Alloy `config.alloy.j2`), Grafana Alloy → Loki, IBM MQ 9.4.5, systemd/journald.

## Global Constraints

- **MQ version:** lab runs **9.4.5**; the JSON stanza services require **MQ ≥ 9.1.0**. **Never** use the `AMQ_ADDITIONAL_JSON_LOG` env var (deprecated 9.0.4 stop-gap).
- **Syslog service values (verbatim):** `Service=Syslog`, `Ident=ibm-mq`, `Severities=all`, `ExcludeMessage=9001,9002` (qm/system surfaces; `ExcludeMessage` is ignored in `mqclient.ini`).
- **Stanza name:** `ClusterSyslog` everywhere.
- **MQ `.ini` files are NOT bracketed INI** — use `ansible.builtin.blockinfile` with marker `# {mark} MQ-DIAG-LOGGING (#282)`, never `ini_file`.
- **Fixed paths:** mqclient.ini = `/var/mqm/mqclient.ini`; mqweb log = `/var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log`.
- **Loki labels stay low-cardinality** (`host`, `unit`); JSON is parsed at query time (`| json`). MQ-core arrives as `unit="ibm-mq"`, mqweb as `unit="ibm-mqweb"`.
- **No silent failures.** journald rate-limiting is disabled on MQ nodes; verification asserts no "Suppressed N messages" marker.
- **Validation command (the only one):** `vrg-container-run -- vrg-validate`.
- **Git:** work in the worktree `.worktrees/issue-282-mq-json-logging`; use `vrg-git` and `vrg-commit` (never raw `git`/`gh`).
- **The human operates the lab.** Any task that provisions VMs, restarts QMs, or runs drills is executed by the human; the agent prepares the exact commands and waits.
- **Cold-rebuild acceptance gate:** lint-green ≠ done. The effort is accepted only after a full cold rebuild of at least one arm proves it one-pass (Task 9).

### Develop fit-check (rebased onto `6a34dbf`, 2026-06-19)

- Verified unchanged on develop, so the anchors below hold: `alloy/templates/config.alloy.j2`, the four `crtmqm` seams (`mq-qmgr`, `mq-pcmk-qmgr`, `mq-nativeha`, `mq-nativeha-spike`), and `mq-client`/`mqweb`/`loki`.
- RDQM creates its QM via `lab/scripts/rdqm-qm-create.sh` (`crtmqm -sx`), extended by the in-flight RDQM HA/DR work (#288). We seed `mqs.ini` at `rdqm-install` and **never edit the script** — no collision.
- `observability.yml` applies `alloy` to `hosts: all` → Task 7 gating is required (see Task 7 note).
- **Pending #286 (build-dir reorg, design-only, not merged):** when it lands, the host-side IBM-docs cache moves `build/refs/` → `build/cache/refs/`. Docs-only touch-up; **no implementation impact** (all runtime paths are guest-side `/var/mqm/...`).

---

### Task 0: Phase 0 spike gate — prove the syslog→journald premise (HUMAN-OPERATED, BLOCKS ALL)

The whole design rests on three unproven facts. **No role build-out (Tasks 1+) starts until this is green.** Operated by the human on one already-provisioned QM node.

**Files:**
- Modify (by hand, on one node, throwaway): `/var/mqm/qmgrs/<QM>/qm.ini`
- Record: append findings to `docs/reports/2026-06-19-mq-json-logging-research.md` (a "Spike findings (Step 2)" section)

**Interfaces:**
- Produces: a go/no-go decision. If `MESSAGE` is not a JSON blob, switch the design to file-tailing `AMQERR0x.json` before Task 6.

- [ ] **Step 1: Add a Syslog diagnostic service to one QM by hand**

On a QM node, as `mqm`, append to `/var/mqm/qmgrs/<QM>/qm.ini`:

```ini
DiagnosticMessages:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
```

- [ ] **Step 2: Restart the QM and generate an event**

```bash
endmqm -i <QM> && strmqm <QM>
echo "STOP LISTENER(L1414)
START LISTENER(L1414)" | runmqsc <QM>
```

- [ ] **Step 3: Capture the journal and confirm the three premises**

```bash
journalctl -t ibm-mq -o json --no-pager | tail -3
journalctl -t ibm-mq -o cat  --no-pager | tail -1 | python3 -m json.tool
```
Expected: (a) MQ entries are present; (b) `SYSLOG_IDENTIFIER` is `ibm-mq`; (c) the `cat` output is a **single valid JSON object** (the `python3 -m json.tool` parse succeeds). Record the actual one-line sample.

- [ ] **Step 4: Confirm mqweb JSON**

Add `<logging messageFormat="json" messageSource="message,ffdc"/>` to that node's `mqwebuser.xml`, `endmqweb && strmqweb`, then:

```bash
tail -1 /var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log | python3 -m json.tool
```
Expected: a single JSON object; the MQ Console/REST still serves over TLS.

- [ ] **Step 5: Record findings + decision**

Append a "Spike findings (Step 2)" section to the research report with the captured samples and an explicit **GO** (premises hold) or **NO-GO → file-tail fallback** decision.

- [ ] **Step 6: Commit**

```bash
cd .worktrees/issue-282-mq-json-logging
vrg-git add docs/reports/2026-06-19-mq-json-logging-research.md
vrg-commit --type docs --scope obs --message "MQ JSON logging spike findings — syslog→journald premise (#282)"
```

---

### Task 1: Create the `mq-diag-logging` role — `system.yml` (mqs.ini template + journald drop-in)

**Files:**
- Create: `ansible/roles/mq-diag-logging/defaults/main.yml`
- Create: `ansible/roles/mq-diag-logging/tasks/system.yml`
- Create: `ansible/roles/mq-diag-logging/handlers/main.yml`

**Interfaces:**
- Produces: role `mq-diag-logging` with `tasks_from: system`. Seeds `mqs.ini` `DiagnosticMessagesTemplate` + `DiagnosticSystemMessages` and disables journald rate-limiting. Consumed by Tasks 5.

- [ ] **Step 1: Write `defaults/main.yml`**

```yaml
---
# JSON diagnostic logging contract (#282). Values are lab-wide; override per-arm only
# for paths (e.g. pcmk's shared-LUN qm.ini via mq_qmini_path at the call site).
mq_diag_ident: ibm-mq
mq_diag_severities: all
mq_diag_exclude: "9001,9002"   # routine channel start/stop chatter
```

- [ ] **Step 2: Write `handlers/main.yml`**

```yaml
---
- name: restart journald
  ansible.builtin.systemd:
    name: systemd-journald
    state: restarted
  become: true
```

- [ ] **Step 3: Write `tasks/system.yml`**

```yaml
---
# System-level JSON diagnostic logging (#282): the DiagnosticMessagesTemplate is
# copied into each new QM's qm.ini at crtmqm, so QMs inherit JSON-to-syslog;
# DiagnosticSystemMessages covers QM-unknown / fallback errors. journald rate
# limiting is disabled so Severities=all is never silently dropped.
- name: seed JSON diagnostic logging template + system stanza in mqs.ini
  ansible.builtin.blockinfile:
    path: /var/mqm/mqs.ini
    marker: "# {mark} MQ-DIAG-LOGGING (#282)"
    block: |
      DiagnosticMessagesTemplate:
         Name=ClusterSyslog
         Service=Syslog
         Ident={{ mq_diag_ident }}
         Severities={{ mq_diag_severities }}
         ExcludeMessage={{ mq_diag_exclude }}
      DiagnosticSystemMessages:
         Name=ClusterSyslog
         Service=Syslog
         Ident={{ mq_diag_ident }}
         Severities={{ mq_diag_severities }}
  become: true
  become_user: mqm

- name: journald drop-in directory
  ansible.builtin.file:
    path: /etc/systemd/journald.conf.d
    state: directory
    mode: "0755"
  become: true

- name: disable journald rate-limiting on MQ nodes (no silent log loss)
  ansible.builtin.copy:
    dest: /etc/systemd/journald.conf.d/10-mq.conf
    mode: "0644"
    content: |
      # MQ JSON logging (#282): Severities=all can burst; never drop silently.
      [Journal]
      RateLimitIntervalSec=0
      RateLimitBurst=0
  become: true
  notify: restart journald
```

- [ ] **Step 4: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (ansible-lint/yamllint clean; new role parses).

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/roles/mq-diag-logging/
vrg-commit --type feat --scope obs --message "mq-diag-logging role: system.yml (mqs.ini JSON template + journald drop-in) (#282)"
```

---

### Task 2: `qmgr.yml` — idempotent `qm.ini` ensure-block

**Files:**
- Create: `ansible/roles/mq-diag-logging/tasks/qmgr.yml`

**Interfaces:**
- Consumes: `mq_qmini_path` (required var, passed by each caller — e.g. `/var/mqm/qmgrs/{{ qmgr_name }}/qm.ini`, or pcmk's `/mqshared/qmgrs/{{ qm_name }}/qm.ini`).
- Produces: `tasks_from: qmgr`. Belt-and-suspenders for re-provisioned QMs; a no-op on freshly-created QMs (the template already populated `qm.ini`).

- [ ] **Step 1: Write `tasks/qmgr.yml`**

```yaml
---
# Belt-and-suspenders: ensure the QM's qm.ini carries the JSON-to-syslog stanza,
# for QMs that pre-date the mqs.ini template. On a freshly created QM this block
# is identical to the inherited content and changes nothing (no restart fires).
- name: assert caller supplied the qm.ini path
  ansible.builtin.assert:
    that: mq_qmini_path is defined
    fail_msg: "mq-diag-logging/qmgr.yml requires mq_qmini_path"

- name: ensure JSON diagnostic logging stanza in qm.ini
  ansible.builtin.blockinfile:
    path: "{{ mq_qmini_path }}"
    marker: "# {mark} MQ-DIAG-LOGGING (#282)"
    block: |
      DiagnosticMessages:
         Name=ClusterSyslog
         Service=Syslog
         Ident={{ mq_diag_ident }}
         Severities={{ mq_diag_severities }}
         ExcludeMessage={{ mq_diag_exclude }}
  become: true
  become_user: mqm
```

- [ ] **Step 2: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/mq-diag-logging/tasks/qmgr.yml
vrg-commit --type feat --scope obs --message "mq-diag-logging: qmgr.yml qm.ini ensure-block (#282)"
```

---

### Task 3: `client.yml` — `/var/mqm/mqclient.ini` stanza

**Files:**
- Create: `ansible/roles/mq-diag-logging/tasks/client.yml`

**Interfaces:**
- Produces: `tasks_from: client`. Adds a marked `DiagnosticSystemMessages` block that coexists with the existing TCP KeepAlive `lineinfile` in `site-distributed-shared.yml`.

- [ ] **Step 1: Write `tasks/client.yml`**

```yaml
---
# Client app diagnostics → syslog as JSON. NOTE: ExcludeMessage/SuppressMessage are
# not honoured in mqclient.ini, so this surface is severity-filtered only. The block
# is marker-delimited so it coexists with the TCP KeepAlive lineinfile on the same file.
- name: ensure /var/mqm exists (client install ran)
  ansible.builtin.file:
    path: /var/mqm
    state: directory
  become: true

- name: JSON diagnostic logging stanza in mqclient.ini
  ansible.builtin.blockinfile:
    path: /var/mqm/mqclient.ini
    create: true
    marker: "# {mark} MQ-DIAG-LOGGING (#282)"
    block: |
      DiagnosticSystemMessages:
         Name=ClusterSyslog
         Service=Syslog
         Ident={{ mq_diag_ident }}
         Severities={{ mq_diag_severities }}
  become: true
  become_user: mqm
```

- [ ] **Step 2: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
vrg-git add ansible/roles/mq-diag-logging/tasks/client.yml
vrg-commit --type feat --scope obs --message "mq-diag-logging: client.yml mqclient.ini stanza (#282)"
```

---

### Task 4: mqweb JSON — add `<logging>` to the Liberty template

The cleanest idempotent home for Liberty config is the existing rendered template, not a post-hoc XML edit. This realizes the spec's `web.yml` surface.

**Files:**
- Modify: `ansible/roles/mqweb/templates/mqwebuser.xml.j2`
- Modify: `ansible/roles/mqweb/defaults/main.yml`

**Interfaces:**
- Consumes: `mqweb_json_logging` (new default, `true`).
- Produces: `messages.log` in single-line JSON at the Installation1 path.

- [ ] **Step 1: Add the default**

In `ansible/roles/mqweb/defaults/main.yml`, append:

```yaml
# JSON diagnostic logging for the mqweb (Liberty) server (#282): single-line JSON
# to messages.log, tailed into Loki as unit=ibm-mqweb.
mqweb_json_logging: true
```

- [ ] **Step 2: Add the `<logging>` element to `mqwebuser.xml.j2`**

Inside the `<server>` element (top level, alongside the existing config), add:

```xml
{% if mqweb_json_logging | default(true) %}
    <!-- #282: JSON diagnostic logging to messages.log (tailed into Loki) -->
    <logging messageFormat="json" messageSource="message,ffdc"/>
{% endif %}
```

- [ ] **Step 3: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (template still renders; markdownlint/yamllint unaffected).

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/mqweb/
vrg-commit --type feat --scope obs --message "mqweb: Liberty messageFormat=json to messages.log (#282)"
```

---

### Task 5: Wire the role into the per-arm QM-creation seams

**Files (Modify):**
- `ansible/roles/mq-qmgr/tasks/main.yml` — system.yml before the `create queue manager` task (line ~3); qmgr.yml after `enable + start the queue-manager service`.
- `ansible/roles/mq-pcmk-qmgr/tasks/main.yml` — system.yml before `create and hand over the queue manager` (line ~28); qmgr.yml after that block, with shared-LUN path.
- `ansible/roles/mq-nativeha/tasks/main.yml` — system.yml after `install base IBM MQ` (line ~9) and before `create the Native HA instance` (line ~24); qmgr.yml after `append the NativeHAInstance peer set to qm.ini` (line ~34).
- `ansible/roles/mq-nativeha-spike/tasks/main.yml` — same shape as mq-nativeha (around its `crtmqm`, line ~104).
- `ansible/roles/rdqm-install/tasks/main.yml` — system.yml as the final task (RDQM inherits at its later `crtmqm -sx`).
- `ansible/roles/mq-client/tasks/main.yml` — client.yml after the client install (after the `pymqi venv` task).

**Interfaces:**
- Consumes: `mq-diag-logging` tasks_from `system`/`qmgr`/`client` (Tasks 1–3). Uses each role's own QM-name var (`qmgr_name` for mq-qmgr; `qm_name` for pcmk/nativeha; `nha_qm` for the spike).

- [ ] **Step 1: mq-qmgr — insert system.yml before `create queue manager`**

```yaml
- name: seed JSON diagnostic logging (mqs.ini template + journald)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: system
```

- [ ] **Step 2: mq-qmgr — insert qmgr.yml after the QM service is started**

```yaml
- name: ensure JSON diagnostic logging in qm.ini
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: qmgr
  vars:
    mq_qmini_path: "/var/mqm/qmgrs/{{ qmgr_name }}/qm.ini"
```

- [ ] **Step 3: mq-pcmk-qmgr — system.yml before QM creation; qmgr.yml after, shared-LUN path**

```yaml
# before "create and hand over the queue manager (first-time only)":
- name: seed JSON diagnostic logging (mqs.ini template + journald)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: system

# after the QM-creation block:
- name: ensure JSON diagnostic logging in qm.ini (shared LUN)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: qmgr
  vars:
    mq_qmini_path: "/mqshared/qmgrs/{{ qm_name }}/qm.ini"
```

- [ ] **Step 4: mq-nativeha — system.yml after install, qmgr.yml after the peer-set append**

```yaml
# after "install base IBM MQ (OS adapter)", before "create the Native HA instance":
- name: seed JSON diagnostic logging (mqs.ini template + journald)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: system

# after "append the NativeHAInstance peer set to qm.ini":
- name: ensure JSON diagnostic logging in qm.ini
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: qmgr
  vars:
    mq_qmini_path: "/var/mqm/qmgrs/{{ qm_name }}/qm.ini"
```

- [ ] **Step 5: mq-nativeha-spike — mirror Step 4 using `nha_qm`**

```yaml
# before the spike crtmqm:
- name: seed JSON diagnostic logging (mqs.ini template + journald)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: system
# after the spike qm.ini blockinfile:
- name: ensure JSON diagnostic logging in qm.ini
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: qmgr
  vars:
    mq_qmini_path: "/var/mqm/qmgrs/{{ nha_qm }}/qm.ini"
```

- [ ] **Step 6: rdqm-install — system.yml as the final task**

RDQM creates its QM via `lab/scripts/rdqm-qm-create.sh` (`crtmqm -sx`), which runs
*after* `rdqm-install`. Seeding `mqs.ini` here means that script's QM inherits the
template — we do **not** edit the script (the RDQM agent is actively extending it
for #288 HA/DR; leave it alone to avoid a collision). **Defer this step** if the
RDQM agent has unmerged `rdqm-install` changes; rebase it in once their branch lands.

```yaml
- name: seed JSON diagnostic logging (mqs.ini template + journald)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: system
```

- [ ] **Step 7: mq-client — client.yml after the client install**

```yaml
- name: JSON diagnostic logging for client apps (mqclient.ini)
  ansible.builtin.include_role:
    name: mq-diag-logging
    tasks_from: client
```

- [ ] **Step 8: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (all modified roles parse; no lint regressions).

- [ ] **Step 9: Commit**

```bash
vrg-git add ansible/roles/mq-qmgr ansible/roles/mq-pcmk-qmgr ansible/roles/mq-nativeha ansible/roles/mq-nativeha-spike ansible/roles/rdqm-install ansible/roles/mq-client
vrg-commit --type feat --scope obs --message "wire mq-diag-logging into all QM-creation seams + client (#282)"
```

---

### Task 6: Alloy — conditional relabel + guarded mqweb file source

**Files:**
- Modify: `ansible/roles/alloy/templates/config.alloy.j2`
- Modify: `ansible/roles/alloy/defaults/main.yml`

**Interfaces:**
- Consumes: `alloy_tail_mqweb` (new default, `false`; set `true` for QM nodes in Task 7).
- Produces: MQ-core journal entries labelled `unit="ibm-mq"`; mqweb file entries labelled `unit="ibm-mqweb"`.

- [ ] **Step 1: Add the default**

In `ansible/roles/alloy/defaults/main.yml`, append:

```yaml
# Tail the mqweb (Liberty) JSON messages.log on queue-manager nodes (#282).
alloy_tail_mqweb: false
```

- [ ] **Step 2: Add the conditional relabel rule**

In `config.alloy.j2`, inside `loki.relabel "journal"`, after the existing `systemd_unit → unit` rule, add:

```alloy
  // #282: MQ logs via syslog have no systemd unit. Fill `unit` from the syslog
  // identifier ONLY when systemd_unit is empty, so cluster-daemon units are untouched.
  rule {
    source_labels = ["__journal__systemd_unit", "__journal__syslog_identifier"]
    separator     = ";"
    regex         = ";(.+)"
    replacement   = "$1"
    target_label  = "unit"
  }
```

- [ ] **Step 3: Add the guarded mqweb file source**

At the end of `config.alloy.j2`, add:

```alloy
{% if alloy_tail_mqweb | default(false) %}
// #282: tail the mqweb (Liberty) JSON messages.log on QM nodes.
local.file_match "mqweb" {
  path_targets = [{
    "__path__" = "/var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log",
    "host"     = "{{ inventory_hostname }}",
    "unit"     = "ibm-mqweb",
  }]
}
loki.source.file "mqweb" {
  targets    = local.file_match.mqweb.targets
  forward_to = [loki.write.obs.receiver]
}
{% endif %}
```

- [ ] **Step 4: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (template renders for both `alloy_tail_mqweb` true/false).

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/roles/alloy/
vrg-commit --type feat --scope obs --message "alloy: conditional syslog-identifier relabel + mqweb messages.log tail (#282)"
```

---

### Task 7: Enable the mqweb tail on QM-node groups

**Files:**
- Modify: the appropriate `ansible/group_vars/<qm-node-group>/…` to set `alloy_tail_mqweb: true` for the groups that run mqweb (pcmk, nativeha, rdqm QM nodes).

**Interfaces:**
- Consumes: `alloy_tail_mqweb` (Task 6).

**Note:** `ansible/observability.yml` applies the `alloy` role to `hosts: all`, so the
default `alloy_tail_mqweb: false` (Task 6) reaches every node — gating it on the
QM-node groups here is **required**, not optional, or non-QM nodes would try to tail
a non-existent `messages.log`.

- [ ] **Step 1: Identify the QM-node groups**

Run: `vrg-git grep -n "alloy" ansible/site-*.yml ansible/observability.yml` and inspect `ansible/group_vars/` to find the groups whose nodes run a QM + mqweb (e.g. `pcmk_a`/`pcmk_b`, `nha_rhel_a`/`nha_rhel_b`, `rdqm_a`/`rdqm_b`).

- [ ] **Step 2: Set `alloy_tail_mqweb: true` for those groups**

Add to each QM-node group_vars file (e.g. `ansible/group_vars/pcmk_a/all.yml`):

```yaml
alloy_tail_mqweb: true
```

- [ ] **Step 3: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/group_vars
vrg-commit --type feat --scope obs --message "enable mqweb messages.log tail on QM-node groups (#282)"
```

---

### Task 8: Install `logcli` on the obs node (acceptance + human debugging tool)

`logcli` is both the Task 9 acceptance tool and a genuinely useful tool for a human
debugging this pipeline (MQ→syslog→journald→relabel→Loki). Install it on the obs
node via the `loki` role so it is always present and reproducible.

**Files:**
- Modify: `ansible/roles/loki/defaults/main.yml` (add `logcli_url`)
- Modify: `ansible/roles/loki/tasks/main.yml` (download + install `logcli`)

**Interfaces:**
- Produces: `/usr/local/bin/logcli` on the obs node, used by Task 9.

- [ ] **Step 1: Add the download URL default**

In `ansible/roles/loki/defaults/main.yml`, append (pin the version to match the
deployed Loki; substitute the real version/arch at execution time):

```yaml
# logcli — Loki CLI for acceptance checks and human debugging of the log pipeline (#282).
logcli_url: "https://github.com/grafana/loki/releases/download/v{{ loki_version }}/logcli-linux-amd64.zip"
```

- [ ] **Step 2: Install logcli in `loki/tasks/main.yml`**

```yaml
- name: download + unpack logcli
  ansible.builtin.unarchive:
    src: "{{ logcli_url }}"
    dest: /tmp
    remote_src: true
    creates: /tmp/logcli-linux-amd64
- name: install the logcli binary
  ansible.builtin.copy:
    src: /tmp/logcli-linux-amd64
    dest: /usr/local/bin/logcli
    mode: "0755"
    remote_src: true
  become: true
```

- [ ] **Step 3: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/loki/
vrg-commit --type feat --scope obs --message "install logcli on obs node for log-pipeline checks + debugging (#282)"
```

---

### Task 9: Cold-rebuild acceptance (HUMAN-OPERATED — the real gate)

**Files:**
- Record: append a "Build verification" note to `docs/reports/2026-06-19-mq-json-logging-research.md`.

**Interfaces:**
- Consumes: everything. This is the acceptance gate — lint-green is not enough.

- [ ] **Step 1: Cold rebuild one arm (e.g. nativeha-rhel) from scratch**

The human runs the arm's create + provision verbs end-to-end (no reuse of prior state).

- [ ] **Step 2: Run a fault drill to generate HA events**

Run the arm's fault suite (e.g. `lab/scripts/nativeha-fault-suite.sh`) to produce real HA/CRR state-change messages.

- [ ] **Step 3: Assert the success criteria**

```bash
# 1. journal JSON shape
journalctl -t ibm-mq -o cat --no-pager | tail -1 | python3 -m json.tool
# 2 & 3. queryable in Loki (run against the obs Loki)
logcli query '{unit="ibm-mq"} | json | ibm_messageId != ""' --limit=5
logcli query '{unit="ibm-mqweb"} | json' --limit=5
# 4. inheritance: freshly created QM has the stanza with no manual step
grep -A4 'MQ-DIAG-LOGGING' /var/mqm/qmgrs/<QM>/qm.ini
# 5. no silent loss
journalctl -t ibm-mq --no-pager | grep -i "Suppressed" && echo "FAIL: rate-limited" || echo "OK: no suppression"
```
Expected: criteria 1–4 return data; criterion 5 prints `OK: no suppression`.

- [ ] **Step 4: Record results + commit**

```bash
vrg-git add docs/reports/2026-06-19-mq-json-logging-research.md
vrg-commit --type docs --scope obs --message "MQ JSON logging build verification — cold-rebuild acceptance (#282)"
```

---

### Task 10: Human debugging runbook for the logging pipeline

A short, stage-by-stage runbook so a human can troubleshoot the pipeline without the
AI ("supportable without the AI" principle). Best written after Task 9 so the
failure-modes table reflects what actually broke during the drill.

**Files:**
- Create: `ansible/roles/... ` — none.
- Create: `docs/reference/mq-logging-debugging.md`

**Interfaces:**
- Consumes: `logcli` (Task 8), the success criteria (Task 9), the flow diagram.

- [ ] **Step 1: Write `docs/reference/mq-logging-debugging.md`**

```markdown
# Debugging the MQ JSON logging pipeline

The path: **MQ Syslog service → `/dev/log` → journald → Alloy (relabel) → Loki**,
plus a separate **mqweb `messages.log` → Alloy file tail → Loki**. Diagram:
`../specs/diagrams/mq-json-logging-flow.html`. Walk the stages in order; the first
one that's empty is your fault domain.

## Stage 1 — is MQ producing JSON?
- Stanza present? `grep -A5 MQ-DIAG-LOGGING /var/mqm/qmgrs/<QM>/qm.ini`
- QM up? `dspmq -m <QM>`  ·  generate one: `echo "STOP LISTENER(L1414)
  START LISTENER(L1414)" | runmqsc <QM>`
- Stanza changes need a QM restart to take effect.

## Stage 2 — did it reach journald?
- `journalctl -t ibm-mq -o cat --no-pager | tail -3`
- Empty? Check the QM emits to syslog (Service=Syslog, not File), and that
  `Ident=ibm-mq`. Confirm journald reads `/dev/log` (default).

## Stage 3 — is it valid single-line JSON?
- `journalctl -t ibm-mq -o cat --no-pager | tail -1 | python3 -m json.tool`
- Fails? The `MESSAGE` field isn't a clean JSON blob — revisit the Phase 0 spike
  finding; consider the file-tail fallback.

## Stage 4 — labelled correctly for Loki?
- The Alloy relabel sets `unit=ibm-mq` only when there's no systemd unit. Inspect
  `/etc/alloy/config.alloy`; restart: `systemctl restart alloy`.

## Stage 5 — is it in Loki?
- `logcli query '{unit="ibm-mq"} | json | ibm_messageId != ""' --limit=5`
- mqweb: `logcli query '{unit="ibm-mqweb"} | json' --limit=5`
- `logcli --addr=http://<obs>:3100 labels unit` to see what labels exist.

## mqweb specifics
- JSON on? `tail -1 .../servers/mqweb/logs/messages.log | python3 -m json.tool`
- Alloy tailing it? `alloy_tail_mqweb: true` for the QM-node group; file path is
  `/var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log`.

## No silent loss
- `journalctl -t ibm-mq --no-pager | grep -i Suppressed` → must be empty. If not,
  the journald drop-in (`/etc/systemd/journald.conf.d/10-mq.conf`) didn't apply;
  `systemctl restart systemd-journald`.

## Common failure modes
| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing in `journalctl -t ibm-mq` | QM not restarted after stanza, or Service=File not Syslog | restart QM; check qm.ini stanza |
| Entries present, `unit` empty in Loki | Alloy relabel rule missing/typo | fix `config.alloy`, restart alloy |
| `python3 -m json.tool` fails on a line | MESSAGE not a JSON blob | revisit spike; file-tail fallback |
| mqweb rows missing | `alloy_tail_mqweb` false, or wrong Installation path | enable on group; fix path casing |
| "Suppressed N messages" in journal | journald drop-in not applied | reapply drop-in; restart journald |
```

- [ ] **Step 2: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (markdownlint clean).

- [ ] **Step 3: Commit**

```bash
vrg-git add docs/reference/mq-logging-debugging.md
vrg-commit --type docs --scope obs --message "human debugging runbook for the MQ logging pipeline (#282)"
```

---

## Self-Review

**Spec coverage:**
- §4.1 role + 4 task files → Tasks 1–4. ✓
- §4.1 wiring at all seams (4 crtmqm roles + rdqm-install + mq-client) → Task 5. ✓
- §4.2 Alloy relabel + mqweb file source → Tasks 6–7. ✓
- §4.3 journald drop-in → Task 1. ✓
- §5 concrete config (all four surfaces + journald) → Tasks 1–4. ✓
- §7 verification → Task 9 (with `logcli` installed in Task 8). ✓
- §1 logcli + human debugging runbook → Tasks 8 & 10. ✓
- §8 Phase 0 spike gate → Task 0 (blocks all). ✓
- §6 pcmk shared-LUN path / native-HA per-node → Task 5 path vars. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full content; commands have expected output. ✓

**Type/name consistency:** marker `# {mark} MQ-DIAG-LOGGING (#282)`, stanza `ClusterSyslog`, vars `mq_diag_ident`/`mq_diag_severities`/`mq_diag_exclude`/`mq_qmini_path`/`alloy_tail_mqweb`, labels `unit="ibm-mq"`/`unit="ibm-mqweb"` — used identically across tasks. ✓

**Open item for execution:** Task 7 Step 1 discovers the exact QM-node group_vars paths (left as a discovery step because group_vars layout is per-arm and is read at execution time).
