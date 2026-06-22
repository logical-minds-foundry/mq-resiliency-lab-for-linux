# Genericize the Message-Domain Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every domain- and vendor-specific identifier in the active working tree (code, lab/ansible config, docs, plans, issues) with a generic request/reply vocabulary, so a fresh clone greps clean of the originating names and the lab reads as a generic HA/DR messaging lab. Behavior is unchanged — only names change.

**Architecture:** A single atomic rename branch (`feature/85-genericize-domain-model`) executed in four phases — (1) the pure-Python domain core, (2) the MQ-object/config/infra layer, (3) docs, (4) issue tracker + a final integration gate. Each phase lands green; the whole branch is the unit of review. This is a **refactor with existing characterization tests** — the discipline per task is *apply rename → prove the existing tests still pass and the targeted tokens are gone → commit*, not red/green TDD (there is no new behavior to test-drive).

**Tech Stack:** Python 3.12 (`mqlab` package, `uv`), IBM MQ (MQSC objects via pymqrest), Ansible (lab provisioning), libvirt (networks/guests), Jinja2 templates, pytest.

**Authoritative spec:** [`docs/specs/2026-06-22-genericize-domain-model-design.md`](../specs/2026-06-22-genericize-domain-model-design.md).

## Global Constraints

- **Git/GH wrappers only.** Use `vrg-git` (not `git`) and `vrg-gh` (not `gh`); raw `git`/`gh` are denied by the permission model. Commit with `vrg-commit --type <type> --scope <scope> --message <msg> [--body <body>]`.
- **Validation is one command.** `vrg-container-run -- vrg-validate` — the only validation entry point. Never run individual linters/formatters. It enforces **100% branch coverage**, ruff, and the topology-integrity tests.
- **Work entirely inside the worktree** `.worktrees/issue-85-genericize-domain-model/` on branch `feature/85-genericize-domain-model`. `cd` into it for any file-touching command.
- **Atomic.** The branch must never be pushed/merged half-renamed. Intermediate commits are fine; a half-renamed *merge* is not. The final gate (Task 14) is the merge precondition.
- **Validation gotchas (from repo memory):** prefer `StrEnum` over `str, Enum` (UP042); respect ruff magic-trailing-comma; run tests via `uv run pytest`; never mask a non-zero exit code in scripts.
- **The "service" discipline rule (spec §2.1):** the word "service" / token `SVC` names tier 3 (the external responder) ONLY. Never rename a tier-2 queue-manager reference to "service".

## Canonical token mapping (the single source of truth — referenced by every task)

| # | Old | New | Notes |
|---|-----|-----|-------|
| M1 | `QMDTCC` | `QMSVC` | external QM (distributed arm) |
| M2 | `QDTCC` (QM name, `content/dtcc-sim.yaml`) | `QMSVC` | external QM (standalone arm) |
| M3 | `QDTCC` (xmitq queue, `qm-main.yaml`) | `QMSVC` | xmitq named after partner QM |
| M4 | `QMAIN.QDTCC` / `QDTCC.QMAIN` | `QMAIN.QMSVC` / `QMSVC.QMAIN` | standalone channels |
| M5 | `{qm}.QMDTCC` / `QMDTCC.{qm}` | `{qm}.QMSVC` / `QMSVC.{qm}` | distributed channels |
| M6 | `DTCC.REQUEST`, `TRADE.REQUEST` | `SVC.REQUEST` | request queue (one canonical) |
| M7 | `TRADE.REPLY`, `FIRM.REPLY` | `APP.REPLY` | reply queue (one canonical) |
| M8 | `SIM.SVRCONN` | `SVC.SVRCONN` | sim responder channel |
| M9 | `dtcc-sim` (guest) | `svc-sim` | + `content/dtcc-sim.yaml`→`svc-sim.yaml` |
| M10 | `net-dtcc` / `virbr-dtcc` | `net-svc` / `virbr-svc` | + `net-dtcc.xml`→`net-svc.xml` (subnet 10.20.0.0/24 unchanged) |
| M11 | `dtcc_received/replied/path/receive_counts/conn/repl/counts/ledger` | `svc_*` | DR fields |
| M12 | `firm_path/confirmed/ledger/states` | `app_*` | DR fields |
| M13 | ledger files `dtcc.jsonl` / `firm.jsonl` | `svc.jsonl` / `app.jsonl` | gitignored, regenerate |
| M14 | `src/mqlab/epn.py`, `tests/test_epn.py` | `src/mqlab/header.py`, `tests/test_header.py` | + `from mqlab.epn` → `from mqlab.header` |
| M15 | `busdate` | `session_date` | header + wire + clients + tests |
| M16 | `trade` (wire field/param), `TRADE-<n>` literals | `payload`, `MSG-<n>` | `wire.py` DRv1 body |
| M17 | `EPN` (header-pattern name, comments/docs) | `fixed-format header (FFH)` | no real-protocol name |
| M18 | `dtcc-org`, `dtcc-responder`, `tls_peer_dtcc` | `svc-org`, `svc-responder`, `tls_peer_svc` | TLS/PKI |
| M19 | `client-org`, `in-house`/`inhouse`, `tls_peer_inhouse` | `app-org`, `app`, `tls_peer_app` | TLS/PKI (full neutralize) |
| M20 | `clearing-service` (cert OU) | `messaging` | neutral OU |
| M21 | `clients/service_responder.py`, `/var/mqm/service_responder.py` | `clients/svc_responder.py`, `/var/mqm/svc_responder.py` | client + deploy path |
| M22 | `mq-service-responder` (systemd unit + `.service.j2`) | `mq-svc-responder` | unit rename |
| M23 | `MQGateway` / `mqgw` | *(deleted)* | purge — see Task 12 |

**Purge gate tokens (must reach the stated count in tracked files):**
- Zero hits, case-insensitive: `dtcc`, `ficc`, `epn`, `mqgateway`, `mqgw`, `busdate`, `clearing`.
- Zero *domain* hits (whole-word / identifier forms): `FIRM`, `TRADE`, `FIRM01` and `firm_*`/`trade_*`/`*.TRADE`/`TRADE.*` (the common-English substrings inside `confirm`/`platform`/`trade-off` are allowed).

## File Structure

Phase 1 (Python core, fully unit-tested):
- `src/mqlab/header.py` (was `epn.py`) — fixed-format header pattern.
- `src/mqlab/dr/wire.py` — DRv1 self-identifying body.
- `src/mqlab/dr/{catalog,classifier,exposure,ledger,model,reconcile,report}.py` — DR framework.
- `src/mqlab/{cli,runplan,setups,dashboard}.py` — CLI + render surfaces.
- `clients/*.py` — lab client scripts.
- `tests/test_*.py` — characterization tests (renamed/updated in lockstep).

Phase 2 (MQ/infra config):
- `lab/networks/net-svc.xml` (was `net-dtcc.xml`); `lab/topology.yaml`; `content/{svc-sim,qm-main}.yaml`.
- `ansible/roles/mq-inter-qm/**`, `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`, `ansible/roles/{mq-exporter,mqweb,prometheus}/**`.
- `ansible/vars/pki-entities.yml`, `ansible/group_vars/all/tls.yml`, `ansible/site-*.yml`, `lab/scripts/*.sh`.

Phase 3 (docs): `docs/site/**`, `docs/{specs,plans,reports,reference,development}/*.md`.

Phase 4: GitHub issues + final integration gate.

---

## Phase 1 — Python domain core

### Task 1: Rename the header module (`epn.py` → `header.py`)

**Mapping:** M14, M15, M17.

**Files:**
- Rename: `src/mqlab/epn.py` → `src/mqlab/header.py`
- Rename: `tests/test_epn.py` → `tests/test_header.py`
- Modify (importers): any file with `from mqlab.epn import` — confirm via grep in Step 1.

- [ ] **Step 1: Find importers and current EPN references**

```bash
cd .worktrees/issue-85-genericize-domain-model
grep -rIn 'mqlab.epn\|from .epn\|import epn\|busdate\|\bEPN\b' src clients tests
```
Expected: the import sites (`clients/dr_flow.py`, `tests/test_epn.py`), the `busdate` field, and the EPN docstring/constants in `epn.py`.

- [ ] **Step 2: Rename the files with git (preserves history)**

```bash
vrg-git mv src/mqlab/epn.py src/mqlab/header.py
vrg-git mv tests/test_epn.py tests/test_header.py
```

- [ ] **Step 3: Edit `src/mqlab/header.py`** — change the module docstring and the `busdate` field to `session_date`. The docstring's first line becomes:

```python
"""Fixed-format header (FFH) pattern (spec 9.1): blank-padded, left-justified
8-char fields - Password, Sender, Receiver, SessionDate - then payload. Field
layouts are per-service and arrive at onboarding; this models the PATTERN."""
```

Then replace every `busdate` identifier with `session_date` (dataclass field, `pack_header`/`parse_header` params, slice assignment, `validate_header`). The `Header` dataclass field becomes `session_date: str`; `pack_header(*, password, sender, receiver, session_date)`; in `validate_header` the staleness check stays (`header.session_date != today`).

- [ ] **Step 4: Update importers** — in `tests/test_header.py` and any client, change `from mqlab.epn import …` → `from mqlab.header import …`, and every `busdate=`/`.busdate` → `session_date=`/`.session_date`. Rename test functions referencing `epn` (e.g. `test_epn_*` → `test_header_*` / `test_ffh_*`).

- [ ] **Step 5: Verify tests pass and tokens are gone**

```bash
uv run pytest tests/test_header.py -v
grep -rIn 'mqlab.epn\|\bEPN\b\|busdate' src tests/test_header.py clients/dr_flow.py
```
Expected: pytest PASS; grep prints nothing for these files (wire/other clients handled in Task 2/3).

- [ ] **Step 6: Commit**

```bash
vrg-commit --type refactor --scope mqlab --message "rename epn header module to header (FFH); busdate->session_date"
```

---

### Task 2: Rename the DRv1 wire body fields (`wire.py`)

**Mapping:** M15, M16.

**Files:**
- Modify: `src/mqlab/dr/wire.py`
- Modify: `tests/test_dr_wire.py`, `tests/test_dr_snapshot.py`
- Modify (callers): `clients/dr_flow.py`, `clients/dr_responder.py`

- [ ] **Step 1: Edit `src/mqlab/dr/wire.py`** to the following (docstring layout, `WireMessage` fields, `build_body`/`parse_body` params/locals):

```python
"""Self-identifying DR message body.

Layout (UTF-8): "DRv1|<seq>|<uuid>|<session_date>|<payload...>"
The payload field is last, so it may contain the '|' delimiter without ambiguity
(we split with maxsplit=4).
"""

from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "DRv1"


@dataclass(frozen=True)
class WireMessage:
    seq: int
    uuid: str
    session_date: str
    payload: str


def build_body(*, seq: int, uuid: str, session_date: str, payload: str) -> bytes:
    return "|".join([_PREFIX, str(seq), uuid, session_date, payload]).encode("utf-8")


def parse_body(body: bytes) -> WireMessage:
    parts = body.decode("utf-8").split("|", 4)
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"not a DRv1 body: {body!r}")
    _, seq, uuid, session_date, payload = parts
    return WireMessage(seq=int(seq), uuid=uuid, session_date=session_date, payload=payload)
```

- [ ] **Step 2: Update callers** — in `clients/dr_flow.py` and `clients/dr_responder.py`, change `build_body(...)`/`parse_body(...)` keyword args `busdate=`→`session_date=`, `trade=`→`payload=`, and any `.busdate`/`.trade` attribute access to `.session_date`/`.payload`. Replace payload literals `f"TRADE-{seq}"` → `f"MSG-{seq}"`.

- [ ] **Step 3: Update tests** — in `tests/test_dr_wire.py` and `tests/test_dr_snapshot.py`, rename `busdate`→`session_date` and `trade`→`payload` in constructors/assertions; update any wire-string literals (`DRv1|…|<date>|TRADE-1` → `…|MSG-1`).

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_dr_wire.py tests/test_dr_snapshot.py -v
grep -rIn 'busdate\|\btrade\b\|TRADE-' src/mqlab/dr/wire.py clients/dr_flow.py clients/dr_responder.py tests/test_dr_wire.py tests/test_dr_snapshot.py
```
Expected: PASS; grep empty.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type refactor --scope dr --message "wire body: busdate->session_date, trade->payload"
```

---

### Task 3: Rename DR framework fields (`dtcc_*`→`svc_*`, `firm_*`→`app_*`)

**Mapping:** M11, M12, M13.

**Files:**
- Modify: `src/mqlab/dr/{catalog,classifier,exposure,ledger,model,reconcile,report}.py`
- Modify: `tests/test_dr_{classifier,end_to_end,exposure,ledger,model,reconcile,report}.py`

- [ ] **Step 1: Enumerate the exact field tokens**

```bash
cd .worktrees/issue-85-genericize-domain-model
grep -rIhoE '\b(dtcc|firm)_[a-z_]+\b' src/mqlab/dr clients | sort -u
grep -rIn "'dtcc'\|\"dtcc\"\|'firm'\|\"firm\"\|dtcc.jsonl\|firm.jsonl" src/mqlab/dr
```
Expected: the field set (`dtcc_received`, `dtcc_replied`, `dtcc_receive_counts`, `dtcc_path`, `dtcc_conn`, `dtcc_repl`, `dtcc_counts`, `dtcc_ledger`, `firm_path`, `firm_confirmed`, `firm_ledger`, `firm_states`) plus any ledger-file string literals.

- [ ] **Step 2: Apply the field rename** across the seven `dr/*.py` modules: every `dtcc_<x>` → `svc_<x>` and every `firm_<x>` → `app_<x>` (dataclass fields, dict keys, JSON ledger keys, function params, local vars). Ledger file literals `dtcc.jsonl`→`svc.jsonl`, `firm.jsonl`→`app.jsonl`. Any bare role strings `"dtcc"`/`"firm"` used as ledger/path discriminators → `"svc"`/`"app"`.

- [ ] **Step 3: Apply the same rename to the matching `tests/test_dr_*.py`** — fixtures, expected dicts, and ledger-key assertions must use the new keys, or coverage/equality assertions fail.

- [ ] **Step 4: Verify the DR suite + branch coverage**

```bash
uv run pytest tests/test_dr_*.py -v
grep -rIn '\bdtcc_\|\bfirm_\|dtcc.jsonl\|firm.jsonl' src/mqlab/dr tests
```
Expected: PASS; grep empty.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type refactor --scope dr --message "rename DR ledger fields dtcc_*->svc_*, firm_*->app_*"
```

---

### Task 4: Remaining `src/mqlab` surfaces + dashboard network literal

**Mapping:** M1–M8 (string literals), M10 (dashboard list), M11 references.

**Files:**
- Modify: `src/mqlab/{cli,runplan,setups,dashboard}.py`
- Modify: `tests/test_{cli_obs,cli_qm,runplan,runreport,dashboard,topology_integrity,topology_nativeha}.py`

- [ ] **Step 1: Enumerate**

```bash
grep -rIn 'dtcc\|DTCC\|QMDTCC\|QDTCC\|net-dtcc' src/mqlab/cli.py src/mqlab/runplan.py src/mqlab/setups.py src/mqlab/dashboard.py
```

- [ ] **Step 2: Apply mapping** — `QMDTCC`→`QMSVC`, `QDTCC`→`QMSVC`, queue/channel literals per M4–M8, and in `dashboard.py:244` the message-path list entry `"net-dtcc"` → `"net-svc"`. Any setup/config keys named `dtcc`→`svc`.

- [ ] **Step 3: Update the matching tests** to the new literals.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_cli_obs.py tests/test_cli_qm.py tests/test_runplan.py tests/test_runreport.py tests/test_dashboard.py tests/test_topology_integrity.py tests/test_topology_nativeha.py -v
grep -rIn 'dtcc\|QMDTCC\|QDTCC' src/mqlab
```
Expected: PASS; grep empty across `src/mqlab`.

- [ ] **Step 5: Full Python gate + commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type refactor --scope mqlab --message "rename remaining DTCC literals in cli/runplan/setups/dashboard"
```
Expected: `vrg-validate` green (Phase 1 complete — the Python core is fully renamed and 100%-covered).

---

## Phase 2 — MQ object & infra config

### Task 5: Rename the `net-dtcc` network

**Mapping:** M10.

**Files:**
- Rename: `lab/networks/net-dtcc.xml` → `lab/networks/net-svc.xml`
- Modify: `lab/topology.yaml` (any `net-dtcc` reference)

- [ ] **Step 1: Rename + edit the XML**

```bash
cd .worktrees/issue-85-genericize-domain-model
vrg-git mv lab/networks/net-dtcc.xml lab/networks/net-svc.xml
```
Edit `lab/networks/net-svc.xml` to:

```xml
<!-- lab/networks/net-svc.xml — external-service-facing net (svc-sim + cluster
     nodes). Isolated: no <forward>, no DHCP. -->
<network>
  <name>net-svc</name>
  <bridge name="virbr-svc"/>
  <ip address="10.20.0.1" netmask="255.255.255.0"/>
</network>
```

- [ ] **Step 2: Update topology references**

```bash
grep -rIn 'net-dtcc\|virbr-dtcc' lab ansible
```
Replace any `net-dtcc`→`net-svc` in `lab/topology.yaml` (the `net-up.sh` glob `net-*.xml` needs no change).

- [ ] **Step 3: Verify + commit**

```bash
grep -rIn 'net-dtcc\|virbr-dtcc' lab ansible src
vrg-commit --type refactor --scope lab --message "rename net-dtcc network to net-svc"
```
Expected: grep empty.

---

### Task 6: Reconcile the standalone `content/` arm to canonical vocabulary

**Mapping:** M2, M3, M4, M6, M7, M8, M9.

**Files:**
- Rename: `content/dtcc-sim.yaml` → `content/svc-sim.yaml`
- Modify: `content/qm-main.yaml`, `lab/topology.yaml` (the `dtcc-sim` node)

- [ ] **Step 1: Rename the sim content file**

```bash
vrg-git mv content/dtcc-sim.yaml content/svc-sim.yaml
```

- [ ] **Step 2: Rewrite `content/svc-sim.yaml`** to (qmgr `QMSVC`; queues/channels canonical):

```yaml
# Declarative MQ objects for QMSVC - the external-service-side reciprocal of qm-main.
qmgr: QMSVC
objects:
  - kind: qlocal
    name: SVC.REQUEST
    attrs: { default_persistence: "yes" }
  - kind: qlocal
    name: QMAIN
    attrs:
      usage: XMITQ
      # Triggered xmitq - see qm-main.yaml QMSVC note (spec 8.3).
      trigger_control: "YES"
      trigger_type: FIRST
      initiation_queue_name: SYSTEM.CHANNEL.INITQ
      trigger_data: QMSVC.QMAIN
  - kind: qremote
    name: APP.REPLY
    attrs:
      default_persistence: "yes"
      remote_queue_name: APP.REPLY
      remote_queue_manager_name: QMAIN
      transmission_queue_name: QMAIN
  - kind: channel
    name: QMSVC.QMAIN
    attrs:
      channel_type: SDR
      transport_type: TCP
      short_retry_interval: 10
      connection_name: 10.20.0.10(1414)
      transmission_queue_name: QMAIN
  - kind: channel
    name: QMAIN.QMSVC
    attrs: { channel_type: RCVR, transport_type: TCP }
  - kind: channel
    name: SVC.SVRCONN
    attrs:
      channel_type: SVRCONN
      transport_type: TCP
      # LAB ONLY: run the channel as mqm so clients clear OAM (spec 1
      # puts security out of scope; never a production posture).
      mca_user: mqm
```

- [ ] **Step 3: Rewrite `content/qm-main.yaml`** — `qmgr: QMAIN` stays; apply: `TRADE.REPLY`→`APP.REPLY`; the xmitq `QDTCC`→`QMSVC` with `trigger_data: QMAIN.QMSVC`; the qremote `DTCC.REQUEST`→`SVC.REQUEST` with `remote_queue_name: SVC.REQUEST`, `remote_queue_manager_name: QMSVC`, `transmission_queue_name: QMSVC`; channels `QMAIN.QDTCC`→`QMAIN.QMSVC` (SDR, conn `10.20.0.50(1414)`, xmitq `QMSVC`) and `QDTCC.QMAIN`→`QMSVC.QMAIN` (RCVR); `APP.SVRCONN` stays.

- [ ] **Step 4: Update the `dtcc-sim` node in `lab/topology.yaml`** — rename the node key `dtcc-sim`→`svc-sim` and its comment; the NICs/subnets are unchanged.

- [ ] **Step 5: Verify + commit**

```bash
grep -rIn 'dtcc\|DTCC\|QDTCC\|TRADE\|FIRM' content lab/topology.yaml
vrg-container-run -- vrg-validate   # runs topology-integrity tests
vrg-commit --type refactor --scope lab --message "reconcile standalone content arm to QMSVC/SVC/APP vocabulary"
```
Expected: grep empty; topology-integrity green.

---

### Task 7: Distributed MQSC templates + vars

**Mapping:** M1, M5, M6, M7 + the `dtcc_conn`→`svc_conn` template var.

**Files:**
- Modify: `ansible/roles/mq-pcmk-qmgr/templates/inter-qm.mqsc.j2`
- Modify: `ansible/roles/mq-inter-qm/templates/their-side.mqsc.j2`, `ansible/roles/mq-inter-qm/tasks/main.yml`
- Modify: any `site-*.yml` / role vars setting `dtcc_conn`

- [ ] **Step 1: Edit `inter-qm.mqsc.j2`** — `QMDTCC`→`QMSVC` (comment, `QREMOTE`, `QLOCAL` xmitq, `TRIGDATA`, both `CHANNEL` names), `DTCC.REQUEST`→`SVC.REQUEST`, and the template var `{{ dtcc_conn }}`→`{{ svc_conn }}`. Result lines:

```jinja
* Our-side inter-QM objects (#147): {{ qm_name }} -> QMSVC across net-ext.
...
DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE
DEFINE QREMOTE(SVC.REQUEST) RNAME(SVC.REQUEST) RQMNAME(QMSVC) XMITQ(QMSVC) REPLACE
DEFINE QLOCAL(QMSVC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA({{ qm_name }}.QMSVC) REPLACE
DEFINE CHANNEL({{ qm_name }}.QMSVC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('{{ svc_conn }}(1414)') XMITQ(QMSVC) SSLCIPH({{ tls_cipher }}) SSLPEER('{{ tls_peer_svc }}') SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL(QMSVC.{{ qm_name }}) CHLTYPE(RCVR) TRPTYPE(TCP) SSLCIPH({{ tls_cipher }}) SSLCAUTH(REQUIRED) SSLPEER('{{ tls_peer_svc }}') REPLACE
```
(Note `tls_peer_dtcc`→`tls_peer_svc` is finalized in Task 8; use the new name here.)

- [ ] **Step 2: Edit `their-side.mqsc.j2`** — the comments referencing QMDTCC/DTCC, `SSLPEER('{{ tls_peer_dtcc }}')`→`SSLPEER('{{ tls_peer_svc }}')`, and the `O=dtcc-org`/`O=client-org` comment text → `O=svc-org`/`O=app-org`. The object names there (`SVC.REQUEST`, `SVC.SVRCONN`, `MON.SVRCONN`) are already canonical.

- [ ] **Step 3: Rename the `dtcc_conn` var** wherever it is *set* (grep `dtcc_conn` across `ansible/`): `dtcc_conn`→`svc_conn`.

- [ ] **Step 4: Verify + commit**

```bash
grep -rIn 'QMDTCC\|DTCC\|dtcc_conn\|tls_peer_dtcc' ansible/roles/mq-pcmk-qmgr ansible/roles/mq-inter-qm
vrg-commit --type refactor --scope ansible --message "rename QMDTCC->QMSVC + dtcc_conn->svc_conn in inter-QM MQSC"
```
Expected: grep empty (TLS DN literals handled in Task 8).

---

### Task 8: TLS/PKI identities — full neutralization

**Mapping:** M18, M19, M20.

**Files:**
- Modify: `ansible/vars/pki-entities.yml`, `ansible/group_vars/all/tls.yml`
- Modify: any template/role referencing `tls_peer_*`, `O=dtcc-org`, `O=client-org`, `clearing-service`

- [ ] **Step 1: Enumerate the full identity surface**

```bash
grep -rIn 'dtcc-org\|dtcc-responder\|client-org\|in-house\|inhouse\|clearing-service\|tls_peer_' ansible
```

- [ ] **Step 2: Apply identity mapping** everywhere:
  - `dtcc-org`→`svc-org` (CN "dtcc-org Root CA"→"svc-org Root CA", `organization_name`, `trust:` lists, comments)
  - `dtcc-responder`→`svc-responder` (cert `cn`, keyrepo path, certlabel)
  - `client-org`→`app-org`; `in-house`/`inhouse`→`app`
  - cert OU `clearing-service`→`messaging`
  - `tls_peer_dtcc`→`tls_peer_svc` (value `"O=dtcc-org"`→`"O=svc-org"`); `tls_peer_inhouse`/`tls_peer_client`→`tls_peer_app` (value `"O=…"`→`"O=app-org"`) — collapse our-side peer vars to one `tls_peer_app` if both currently resolve to the client-org DN; otherwise rename 1:1. Confirm by reading `tls.yml` in Step 1.

- [ ] **Step 3: Update consumers** — re-grep for the old var names across templates and replace (the `inter-qm`/`their-side` `.j2` already moved to `tls_peer_svc` in Task 7; catch any others, e.g. exporter MON.SVRCONN peer).

- [ ] **Step 4: Verify + commit**

```bash
grep -rIn 'dtcc\|client-org\|in-house\|inhouse\|clearing' ansible
vrg-commit --type refactor --scope tls --message "neutralize PKI identities: dtcc-org->svc-org, client-org->app-org, OU->messaging"
```
Expected: grep empty.

---

### Task 9: `service_responder` → `svc_responder` + systemd unit

**Mapping:** M21, M22.

**Files:**
- Rename: `clients/service_responder.py` → `clients/svc_responder.py`
- Rename: `ansible/roles/mq-inter-qm/templates/mq-service-responder.service.j2` → `mq-svc-responder.service.j2`
- Modify: `ansible/roles/mq-inter-qm/tasks/main.yml`, `lab/scripts/e2e-test.sh`

- [ ] **Step 1: Rename the client + service template**

```bash
vrg-git mv clients/service_responder.py clients/svc_responder.py
vrg-git mv ansible/roles/mq-inter-qm/templates/mq-service-responder.service.j2 \
           ansible/roles/mq-inter-qm/templates/mq-svc-responder.service.j2
```

- [ ] **Step 2: Edit `clients/svc_responder.py`** — apply any DTCC/FIRM/TRADE/busdate tokens inside it per the mapping (it was in the `DTCC`+`dtcc`+`FIRM`+`busdate` file lists), and `--keyrepo …/dtcc-responder`/`--certlabel dtcc-responder` defaults → `svc-responder`.

- [ ] **Step 3: Edit `ansible/roles/mq-inter-qm/tasks/main.yml`** — `service_responder.py`→`svc_responder.py` (copy src + dest `/var/mqm/svc_responder.py`), the template `mq-service-responder.service.j2`→`mq-svc-responder.service.j2`, dest `/etc/systemd/system/mq-svc-responder.service`, and `name: mq-svc-responder` (the systemd handler/start).

- [ ] **Step 4: Edit `mq-svc-responder.service.j2`** — `ExecStart` path `/var/mqm/svc_responder.py`, `--keyrepo /var/mqm/ssl/svc-responder --certlabel svc-responder`. (`--in-queue SVC.REQUEST --channel SVC.SVRCONN` already canonical.)

- [ ] **Step 5: Edit `lab/scripts/e2e-test.sh`** — the "DTCC service responder … mq-service-responder" comment → "external-service responder … mq-svc-responder".

- [ ] **Step 6: Verify + commit**

```bash
grep -rIn 'service_responder\|mq-service-responder\|dtcc\|DTCC' clients/svc_responder.py ansible/roles/mq-inter-qm lab/scripts/e2e-test.sh
vrg-commit --type refactor --scope ansible --message "rename service_responder->svc_responder + mq-svc-responder unit"
```
Expected: grep empty.

---

### Task 10: Lab scripts, site playbooks, remaining roles

**Mapping:** M1, M6, M7, M11, plus residual `dtcc`/`FIRM`/`TRADE` tokens.

**Files:**
- Modify: `lab/scripts/{dr-run,lab-snapshot,pcmk-dr-cutover,rdqm-qm-create}.sh`
- Modify: `ansible/site-{distributed,distributed-shared,nativeha-distributed,nativeha-dr,rdqm-distributed,obs}.yml`, `ansible/gather-versions.yml`
- Modify: `ansible/roles/{prometheus/templates/prometheus.yml.j2,mq-exporter/{defaults,tasks}/main.yml,mqweb/tasks/main.yml}`

- [ ] **Step 1: Enumerate residuals**

```bash
grep -rIn 'dtcc\|DTCC\|QMDTCC\|QDTCC\|\bFIRM\b\|\bTRADE\b' lab/scripts ansible/site-*.yml ansible/gather-versions.yml ansible/roles/prometheus ansible/roles/mq-exporter ansible/roles/mqweb
```

- [ ] **Step 2: Apply the mapping** file-by-file. Key spots: `dr-run.sh` `--in-queue TRADE.REQUEST --out-queue FIRM.REPLY`→`--in-queue SVC.REQUEST --out-queue APP.REPLY` and `--reply-queue TRADE.REPLY`→`APP.REPLY`; `prometheus.yml.j2` scrape target/job `QMDTCC`→`QMSVC`; site playbooks `dtcc_conn`/host aliases/`QMDTCC`→`svc`/`QMSVC`.

- [ ] **Step 3: Verify + commit**

```bash
grep -rIn 'dtcc\|DTCC\|QMDTCC\|QDTCC\|\bFIRM\b\|\bTRADE\b' lab/scripts ansible
vrg-container-run -- vrg-validate
vrg-commit --type refactor --scope lab --message "rename residual DTCC/FIRM/TRADE in scripts, site playbooks, roles"
```
Expected: grep empty across `lab/scripts` + `ansible`; `vrg-validate` green (Phase 2 complete).

---

## Phase 3 — Docs

### Task 11: Living docs + the service-discipline glossary

**Files:**
- Modify: `docs/site/docs/getting-started.md`, `docs/site/docs/architecture/index.md`, `docs/reference/*.md` (the lab-facing reference set), `docs/development/lab-bringup-capture.md`

- [ ] **Step 1: Enumerate**

```bash
grep -rIn 'dtcc\|DTCC\|QMDTCC\|QDTCC\|\bEPN\b\|\bFIRM\b\|\bTRADE\b\|busdate' docs/site docs/reference docs/development
```

- [ ] **Step 2: Apply the mapping** in prose + code blocks. Replace `EPN`→"fixed-format header (FFH)"; identifiers per M1–M22; reword any "DTCC"/"vendor" narrative to "the external service".

- [ ] **Step 3: Add the glossary note** (spec §2.1) to `docs/site/docs/architecture/index.md`:

> **"service" — two senses.** In this lab, *the external service* (`SVC`) is the request/reply responder our queue manager exchanges messages with. Our own queue manager is *not* called "the service" — it is "the queue manager" / "the broker" that serves connected MQ clients.

- [ ] **Step 4: Verify + commit**

```bash
grep -rIn 'dtcc\|DTCC\|\bEPN\b\|busdate' docs/site docs/reference docs/development
vrg-commit --type docs --scope site --message "genericize living docs + add service-sense glossary"
```
Expected: grep empty (whole-word FIRM/TRADE checked too).

---

### Task 12: Dated specs/plans/reports — anonymize + delete

**Files:**
- Delete: `docs/development/2026-06-17-mqgateway-datagram-requirements.md` (M23)
- Delete: any deep-dive originating-entity research / `EPN-MQ-Implementation-Guide` notes file; strip the five `dtcc.com` URLs
- Modify: all remaining tracked `docs/**/*.md` mentioning the tokens (anonymize in place); rewrite the unimplemented plan docs

- [ ] **Step 1: Delete the purge-only docs**

```bash
cd .worktrees/issue-85-genericize-domain-model
vrg-git rm docs/development/2026-06-17-mqgateway-datagram-requirements.md
grep -rIln 'EPN-MQ-Implementation-Guide\|dtcc.com' docs   # identify research/URL-bearing files
```
Delete any file that is *substantially* originating-entity research; for files that merely *cite* `dtcc.com`, remove the URL/citation lines only.

- [ ] **Step 2: Rename the DTCC-named plan doc**

```bash
vrg-git mv docs/plans/2026-06-13-plan-2-dtcc-qm-inter-qm-channels.md \
           docs/plans/2026-06-13-plan-2-svc-qm-inter-qm-channels.md
```

- [ ] **Step 3: Anonymize all remaining docs in place.** Enumerate and work the list:

```bash
grep -rIl 'dtcc\|DTCC\|QMDTCC\|QDTCC\|\bEPN\b\|MQGateway\|mqgw' docs
```
For each file, apply M1–M23 to identifiers and reword `DTCC`/`vendor`/`EPN`/`MQGateway` narrative to the generic vocabulary. Unimplemented plan docs (`plan-2-…`, `plan-3-app-requester-end-to-end.md`, `2026-06-17-vendor-two-site-dr.md`, the RDQM/native-ha distributed plans) get the same treatment so future work inherits the new names. Replace any sourcing rationale with the unnamed generic form ("derived from a real-world clearing/payments resilience scenario" → keep generic; do **not** name an entity).

- [ ] **Step 4: Verify the docs gate + commit**

```bash
grep -rIcE 'dtcc|ficc|epn|mqgateway|mqgw|busdate|clearing' docs | awk -F: '$2>0'
vrg-commit --type docs --scope all --message "anonymize dated specs/plans/reports; purge MQGateway + dtcc.com research"
```
Expected: the `awk` filter prints nothing (zero hits for the unambiguous tokens across `docs`).

---

## Phase 4 — Tracker + final gate

### Task 13: Update the issue tracker

**Files:** GitHub issues (no repo files).

- [ ] **Step 1: Update titles/bodies** of the open issues that bake in old names, using `vrg-gh issue edit`:
  - #147 (`QMDTCC`→`QMSVC`), #148 (`QMAIN`/`EPN`), #237 (vendor/DTCC two-site DR), #267 (converge), #145, #146, #149, #182.
  - For each: `vrg-gh issue view <N> --json title,body`, apply the mapping, `vrg-gh issue edit <N> --title … --body …`.

- [ ] **Step 2: Close #73** as superseded:

```bash
vrg-gh issue comment 73 --body "Superseded by #85 and the reconciled spec docs/specs/2026-06-22-genericize-domain-model-design.md (single coordinated rename). Closing as folded-in."
vrg-gh issue close 73
```

- [ ] **Step 3: No commit** (tracker-only). Note completion in the PR description.

---

### Task 14: Final integration gate (merge precondition)

- [ ] **Step 1: Whole-tree purge gate** — must be clean:

```bash
cd .worktrees/issue-85-genericize-domain-model
echo "== unambiguous tokens (expect ZERO) =="
grep -rIcE 'dtcc|ficc|epn|mqgateway|mqgw|busdate|clearing' . \
  --exclude-dir=.git --exclude-dir=build | awk -F: '$2>0'
echo "== domain identifiers (expect ZERO) =="
grep -rInE '\bFIRM[0-9]*\b|\bTRADE\b|firm_[a-z]|trade_[a-z]|\.TRADE\b|\bTRADE\.' . \
  --exclude-dir=.git --exclude-dir=build
```
Expected: both print nothing. Investigate and fix any straggler before proceeding.

- [ ] **Step 2: Full validation**

```bash
vrg-container-run -- vrg-validate
```
Expected: green at 100% branch coverage.

- [ ] **Step 3: Cold-rebuild acceptance of the message path** (the repo's cold-rebuild gate — lint-green is not "done" for provisioning changes). Hand to the human operator per the lab-ops boundary:
  - Rebuild the standalone arm: `net-svc` up, `svc-sim` (`QMSVC`) + `QMAIN` provisioned, `APP → QMAIN → QMSVC → APP.REPLY` round-trips.
  - Rebuild a distributed arm (pcmk): `QMSVC` inter-QM channels start, `mq-svc-responder` runs, certs issue under `svc-org`/`app-org`, end-to-end reply returns.

- [ ] **Step 4: Rebase + sanity-check onto current `develop`** (spec §7 — develop is clean; this is the next change), resolve any drift, re-run Step 1–2.

- [ ] **Step 5: Open the PR**

```bash
vrg-gh pr create --base develop --head feature/85-genericize-domain-model \
  --title "Genericize the message-domain model (DTCC/FIRM/EPN -> SVC/APP/FFH)" \
  --body "Implements docs/specs/2026-06-22-genericize-domain-model-design.md. Atomic de-brand: grep-clean working tree, behavior unchanged. Closes #85, supersedes #73."
```

---

## Self-Review

**Spec coverage** (spec §-by-§ → task):
- §2/§3 vocabulary → Tasks 1–10 (mapping M1–M22) + §2.1 glossary in Task 11. ✓
- §4.1 MQ objects → Tasks 4, 6, 7. ✓
- §4.2 guests/topology/networks → Tasks 5, 6. ✓
- §4.3 DR fields/ledgers → Tasks 2, 3. ✓
- §4.4 header module/clients/tests → Tasks 1, 2, 9. ✓
- §5 docs de-id (anonymize + delete + URL strip + MQGateway purge) → Tasks 11, 12. ✓
- §6 plans + issues (rewrite unimplemented plans; update issues; close #73) → Tasks 12, 13. ✓
- §7 execution (atomic branch, rebase, re-provision, validate, cold-rebuild) → Task 14. ✓
- §8 out of scope (repo rename #84, git history, CLI reorg, inbound flow) → not implemented (correct). ✓
- **Surface beyond spec §4** found in planning (net-dtcc, standalone QDTCC arm, TLS identities, systemd unit, wire fields, richer DR fields) → Tasks 5, 6, 8, 9, 2, 3. ✓

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Hand-edited critical files (`header.py`, `wire.py`, `net-svc.xml`, `svc-sim.yaml`, `inter-qm.mqsc.j2`) show literal target content; mechanical bulk uses verbatim mapping tables + exact verify commands.

**Type/name consistency:** `session_date` and `payload` are used identically across `header.py`, `wire.py`, callers, and tests; `QMSVC`/`SVC.REQUEST`/`APP.REPLY` used consistently in content + MQSC + scripts; `tls_peer_svc`/`svc-org`/`app-org` consistent across Tasks 7–8. The xmitq-named-after-remote-QM convention (`QMSVC` on QMAIN's side, `QMAIN` on QMSVC's side) is preserved from the originals.
