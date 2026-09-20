# Startup readiness-budget inventory — right-sizing & fatality

**Date:** 2026-09-20
**Issue:** #1162 (epic logical-minds-foundry/.github#249 — macOS/arm64 startup
reliability & bring-up hardening)
**Task:** Plan Tasks 3–4 (B — budgets). Task 3 inventories every service's systemd
`TimeoutStartSec` and its paired Ansible readiness wait, right-sizes each to the
nested-virt reality (generous-but-bounded, never `infinity`, never a silent default
for a heavy service), and sets fatality by data-path dependence. Task 4 pins the
result with `tests/test_startup_budgets.py`.

**Status:** DECIDED. Every budget value below is cited to the file:line it is read
from or written to (repo, this branch). Where a decision rests on reasoning rather
than a value read from the code, it is labelled **(judgment)**.

**Method note:** the lab is human-operated and this is static/code work — no live
boot was performed for this note. The right-sizing rests on the epic's recorded
cold-boot measurements (spec §1, §4.3) and the already-landed logsearch budgets
(#1034/#1040, epic .github#198), not on a fresh run. The cold-rebuild acceptance
gate (VAL-A #1154 / VAL-B #1155) is what proves the budgets on real hardware;
`vrg-validate` green is necessary, not sufficient (spec §7).

---

## 1. The nested-virt JVM cold-start tax (the sizing basis)

**(data, from spec §1 / §4.3):** on macOS → Lima → libvirt/KVM → guest, JVM
cold-start pays ~7× the flat-KVM cloud — OpenSearch cold-start ~225 s on 6 vCPU vs
~30 s on the cloud; cold-boot run #1022 measured the OpenSearch JVM bootstrap swing
from ~50 s to ~8.5 min under host oversubscription. The cost is CPU-active-but-slow
(iowait ~0), and it **compounds under concurrency** — several JVMs cold-starting at
once each pay the tax simultaneously and blow their budgets.

**(judgment):** a readiness budget for a JVM-heavy service must clear the measured
worst case (~8.5 min) with headroom, hence the ~15-minute (900 s) budget the
logsearch tier already adopted (#1034/#1040). Budgets stay **bounded** (never
`infinity`) so a genuinely-hung service still fails loud, and stay **generous**
enough that the fast x86 cloud never trips them (parity, spec §3 / risk §8).

---

## 2. Full inventory (every service unit + its paired readiness wait)

### 2.1 JVM-heavy services (the budget-bearing tier)

| Service | Node(s) | systemd `TimeoutStartSec` | Readiness wait | Fatality | Unit source | Wait source |
|---|---|---|---|---|---|---|
| mqweb / Liberty | MQ QM nodes | **1800** | `wait_for` :9443, 300 s | **non-fatal** | `mqweb/templates/mqweb.service.j2:13` | `mqweb/tasks/main.yml:103` |
| OpenSearch | logsearch | **900** *(was 180 — reconciled, §3)* | `uri` `_cluster/health`, 180×5 = **900 s** | **fatal** | `opensearch/tasks/install.yml:120` | `opensearch/tasks/configure.yml:38` |
| OpenSearch Dashboards | logsearch | **900** | `uri` `/api/status`, 180×5 = **900 s** | **fatal** | `opensearch-dashboards/tasks/install.yml:90` | `opensearch-dashboards/tasks/configure.yml:51` |
| Data Prepper | logsearch | **900** | `wait_for` :21892, **900 s** | **fatal** | `data-prepper/tasks/install.yml:98` | `data-prepper/tasks/configure.yml:28` |

### 2.2 IBM MQ queue managers (native, not JVM)

| Service | Node(s) | systemd `TimeoutStartSec` | Readiness wait | Fatality | Unit source |
|---|---|---|---|---|---|
| qm (`strmqm`) | single-instance QM nodes (rdqm/pcmk base) | **300** | none — `Type=forking`, systemd waits on the fork | **fatal** (forking) | `mq-qmgr/templates/qm.service.j2:10` |
| qm (pcmk-managed) | pcmk QM nodes | **300** | Pacemaker resource monitor | **fatal** | `mq-pcmk-qmgr/tasks/main.yml:248` |
| Native HA QM | nativeha nodes | **no systemd unit** | `strmqm` + Native HA quorum (not systemd-started) | n/a | — |

### 2.3 Peripheral services (lightweight, `Type=simple`, ride the 90 s default)

All of the following are `Type=simple` (or default) Go / Python / Node processes.
For a `Type=simple` unit systemd marks the service **active the instant it execs**,
so `TimeoutStartSec` is **non-gating** — the 90 s default never fires on them. None
is a JVM or JVM-adjacent, so none is bumped (§3, judgment).

| Service | Kind | Unit source |
|---|---|---|
| mq-exporter (`mq_prometheus`) | Go | `mq-exporter/templates/mq-exporter.service.j2` |
| app-requester | Python (MQ client) | `app-requester/templates/mq-app-requester.service.j2` |
| mq-svc-responder@ | Python (MQ client) | `mq-inter-qm/templates/mq-svc-responder@.service.j2` |
| mqmonitor@ | Python (MQ client) | `mq-nativeha/tasks/main.yml:95` |
| node_exporter | Go | `node-exporter/templates/node_exporter.service.j2` |
| alloy | Go | `alloy/tasks/install.yml:56` |
| loki | Go | `loki/tasks/install.yml:59` |
| prometheus | Go | `prometheus/tasks/install.yml:60` |
| grafana-image-renderer | Node/chromium | `grafana-image-renderer/tasks/install.yml:64` |
| grafana-server | Go (packaged) | packaged unit + drop-ins `grafana/tasks/configure.yml` |

---

## 3. Right-sizing decisions

### 3.1 OpenSearch unit 180 → 900 (the one value change)

**(data):** the OpenSearch unit declared `TimeoutStartSec=180`
(`opensearch/tasks/install.yml:120`) while its real readiness gate is the fatal
900 s `uri` loop (180 retries × 5 s, `configure.yml:38`). Dashboards and Data
Prepper units were already 900 (#1040); only OpenSearch was left at the old 180.

**(judgment):** the two numbers described the same service with two different
budgets — the inconsistency spec §4.3 note 2 flags. Because the unit is
`Type=simple`, the 180 was already inert (systemd never gated on it), so this is a
**declared-budget reconciliation, not a behaviour change**: the 900 s `uri` loop
remains the effective, fatal OpenSearch readiness budget. Set the unit to 900 so a
future reader sees one coherent budget and a future edit can't quietly re-introduce
a 180 s figure the guardrail would then miss. **Changed on this branch.**

### 3.2 mqweb 1800 / 300 — kept (already right-sized by #1151)

**(data):** unit `TimeoutStartSec=1800` (`mqweb.service.j2:13`), non-blocking start
(`no_block: true`, `main.yml:69`), bounded **non-fatal** readiness gate `wait_for`
:9443 300 s with `failed_when: false` + a `debug` warn keyed on
`elapsed >= 300` (`main.yml:103-119`). **(judgment):** left unchanged — #1151 sized
this for the Liberty JVM under TCG; the 1800 s unit is generous-but-bounded and the
300 s gate is deliberately non-fatal (§4.1).

### 3.3 logsearch trio 900 / 900 — kept

**(data/judgment):** OpenSearch, Dashboards, Data Prepper units and gates all sit at
the 900 s (~15 min) budget aligned in #1034/#1040 to clear the ~8.5 min measured
cold-start with headroom. Bounded, not `infinity`; unchanged beyond §3.1's unit
reconciliation.

### 3.4 qm 300 — kept

**(data):** `qm.service.j2:10` and the pcmk-managed unit
(`mq-pcmk-qmgr/tasks/main.yml:248`) both declare `TimeoutStartSec=300` on a
`Type=forking` unit. **(judgment):** `strmqm` is native C, not a JVM — it does not
pay the JVM cold-start tax — and 300 s is generous for it even under nested virt.
`Type=forking` means systemd already waits on (and fails loud on) the fork, so the
budget is both bounded and fatal, matching the message-path dependence. Unchanged.

### 3.5 Peripheral `Type=simple` services — 90 s default kept (documented)

**(judgment):** the doctrine's "no silent default" (spec §3) targets *heavy*
services that can actually miss a budget. Every peripheral service (§2.3) is
`Type=simple`, so `TimeoutStartSec` is non-gating — systemd marks them active on
exec and the 90 s default is never consulted as a readiness deadline. None is a JVM
or JVM-adjacent, so none silently rides a too-tight budget. Bumping them would add
churn with zero behavioural effect. Left at the default, recorded here so the
decision is explicit rather than an oversight. If any of these is later converted to
`Type=notify`/`Type=forking`, it must gain an explicit bounded budget at that time.

---

## 4. Fatality by data-path dependence

The doctrine (spec §3): **fail loud** where the message/data path depends on the
service; **non-fatal-with-warn** where it does not. The current fatality already
matches data-path dependence across the board — no fatality was flipped; the calls
are recorded here per service.

- **mqweb — non-fatal (correct).** The MQ **message path does not depend on the
  REST/console** (mqweb is admin/observability surface). The gate is
  `failed_when: false` + a `debug` warn (`main.yml:103-119`); provision proceeds.
- **OpenSearch / Dashboards / Data Prepper — fatal (correct).** These form the
  **log-ingestion data path** (Alloy → Data Prepper → OpenSearch store ← Dashboards
  reads it). A half-up logsearch tier is a silent-failure hazard — #1040 made these
  gates abort rather than "limp forward." The `uri`/`wait_for` loops carry no
  `failed_when: false`, so a never-ready tier fails the play loud.
- **qm — fatal (correct).** The message path *is* the queue manager. `Type=forking`
  makes systemd wait on the fork and fail the unit if `strmqm` does not complete.
- **Peripherals — n/a.** `Type=simple` + `Restart=` recovery; not gated at bring-up
  and not on the critical message path (exporters/monitors are observability
  producers; a missed sample self-heals on the next scrape).

---

## 5. Guardrail (Task 4)

`tests/test_startup_budgets.py` pins the reconciled budgets so a future edit cannot
silently drift a heavy-service budget to `infinity` or the 90 s default:

- each JVM-heavy unit declares a bounded `TimeoutStartSec` inside a `MIN ≤ v ≤ MAX`
  range (not merely `!= 180`);
- each unit's paired Ansible readiness wait matches the value in §2 (the `wait_for`
  timeout, or `retries × delay` for a `uri`/`until` loop);
- the statically-checkable fatality holds — mqweb's gate carries `failed_when: false`
  (non-fatal), while the logsearch trio's gates do **not** (fatal).

It complements `tests/test_logsearch_budgets.py` (dashboards + data-prepper units &
waits) and `tests/test_opensearch_render.py` (OpenSearch `retries==180`/`delay==5`)
by covering the gap those left: the **mqweb** unit (1800) + non-fatal gate (300) and
the **OpenSearch unit** (now 900), plus the fatality assertions.

---

## 6. Change summary

- **Code:** `opensearch/tasks/install.yml` — unit `TimeoutStartSec` 180 → 900 (§3.1).
- **Test:** `tests/test_startup_budgets.py` — new guardrail (§5).
- **Docs:** this note.
- **No other budget or fatality changed** — the rest of the inventory was already
  right-sized and correctly fatal; this note records the confirming judgment.
