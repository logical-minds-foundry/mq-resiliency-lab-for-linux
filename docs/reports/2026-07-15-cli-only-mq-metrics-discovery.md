# CLI-only / filesystem-only MQ state worth mapping to metrics — discovery

> **Issue:** #646 (task **T16**) · **Epic:** `logical-minds-foundry/.github`#79 ·
> **Date:** 2026-07-15 · **Status:** Discovery captured — **discovery-only**,
> builds nothing this epic; feeds follow-on brainstorm #81.
> **Scope:** IBM MQ **9.4** on distributed (Linux) queue managers. Enumerate and
> prioritize **non-MQI** operational state — state exposed *only* via a control
> command (`dspmq*`/`dmpmqcfg`/`rdqmstatus`) or *only* on the local
> filesystem/config — that is **not** already covered by the stock IBM
> `mq_prometheus` exporter, and is therefore a candidate for the
> `mq-resiliency-observability` component to map into Prometheus metrics.

---

## 0. How to read this report

Every MQ-behavior claim is labelled **[data]** (what an IBM MQ 9.4 page actually
says, cited) vs **[judgment]** (reasoning on top of those facts — metric shapes,
priorities, absence calls). The user fact-checks research against primary
sources, so the separation is deliberate.

**Provenance.** All **[data]** claims are quoted from IBM MQ 9.4.x documentation
fetched with `tools/ibm_doc_cache.py` (plain `WebFetch` is HTTP-403'd by IBM
Docs) and cached under `build/refs/ibm-docs/ibm-mq/9.4.x/<slug>/content.txt`
(gitignored; not redistributed). Each is cited by slug + `source_url`. Retrieval
date: **2026-07-15**. Full source list in §7. Lab-internal facts cite in-repo
paths.

---

## 1. Charter — the non-MQI boundary

**[judgment]** The stock exporter this component sits *beside* is IBM's
`mq_prometheus` (from `github.com/ibm-messaging/mq-metric-samples`), deployed in
this lab **client-mode on the off-cluster `mon-probe` node** against each queue
manager's VIP (`ansible/roles/mq-exporter/`, plan
`docs/plans/2026-06-14-mq-prometheus-exporter.md`). It sources everything it
emits from **MQI** — the `$SYS/…` statistics/monitoring publications plus
`DISPLAY`-type PCF inquiries — and publishes the `ibmmq_*` families the lab's
Grafana boards consume: `ibmmq_qmgr_status`, `ibmmq_qmgr_connection_count`,
`ibmmq_qmgr_active_listeners`, `ibmmq_queue_depth`,
`ibmmq_queue_oldest_message_age`, `ibmmq_queue_uncommitted_messages`,
`ibmmq_channel_status`, and the rest (enumerated across `src/mqlab/qmboard.py`,
`messagingboard.py`). Enablement of the underlying statistics is documented in
`docs/site/docs/guides/mq-metrics-config-guide.md`.

**[judgment]** Because `mq_prometheus` never shells out to a control command and
never reads the MQ data directory, **any state reachable only via CLI output or
only on the filesystem is absent from it by construction.** That is the charter
axiom of this component: it owns the non-MQI half. This report's "genuinely
absent?" column therefore reduces to two checks per candidate: (a) is the state
truly CLI/filesystem-only (no MQI/PCF equivalent the exporter already publishes),
and (b) is it not already collected by the **lab's own** non-MQI collectors (see
§2).

## 2. What the lab already collects on the non-MQI side (not candidates)

**[judgment]** The lab already runs its *own* textfile-dropzone collectors —
`src/mqlab/nativehastate.py`, `rdqmstate.py`, `clusterstate.py` — which parse
control-command / cluster-tool output into `cluster_nha_*`, `cluster_rdqm_*`,
`cluster_drbd_*` node_exporter series (roadmap
`docs/specs/2026-06-27-component-extraction-roadmap-design.md` §4.1; these
collectors are the seed of the future `mq-resiliency-observability` bundle).
Consequently the following non-MQI state is **already mapped** and is **not** a
candidate for new work here — listing it explicitly so the follow-on does not
re-map it:

- **RDQM HA/DR replication state** — `rdqmstatus` (HA/DR role, node
  online/offline, DRBD sync, failed resource actions) is parsed by
  `rdqmstate.py` (`parse_rdqmstatus`, tested in `tests/test_rdqmstate.py`).
- **Native HA quorum/replication state** — `dspmq -o nativeha -x` is parsed by
  `nativehastate.py`.
- **Bare QM run-state / standby** — covered both by `dspmq` parsing and by the
  exporter's `ibmmq_qmgr_status`.

The candidates below are the state that **neither** `mq_prometheus` **nor** the
lab's existing collectors cover today.

## 3. Candidate summary (prioritized)

| # | Candidate | Source (CLI / path) | Proposed metric(s) | Absent from `mq_prometheus`? | Priority |
|---|-----------|---------------------|--------------------|------------------------------|----------|
| 1 | **FFST/FDC files** | `/var/mqm/errors/*.FDC` (+ per-QM `…/qmgrs/<QM>/errors`) | `mq_ffst_files` (gauge), `mq_ffst_oldest_age_seconds` (gauge) | Yes — filesystem-only | **High** |
| 2 | **In-doubt transactions** | `dspmqtrn -m <QM>` | `mq_indoubt_transactions` (gauge, by coordination/state) | Yes — no MQI metric emitted | **High** |
| 3 | **Config drift** | `dmpmqcfg -m <QM>` | `mq_config_objects` (gauge), `mq_config_drift` (gauge 0/1), `mq_config_dump_timestamp_seconds` | Yes — CLI-only | **Med** |
| 4 | **Install / patch inventory** | `dspmqver`, `dspmqinst` | `mq_installation_info` (info gauge=1, VRMF/level/type labels), `mq_installation_entitlement_info` | Yes — CLI-only | **Med-Low** |
| 5 | **System error-log rate** | `/var/mqm/qmgrs/<QM>/errors/AMQERR0*.LOG` | `mq_errorlog_messages_total` (counter, by severity) | Partly — see §4.5 seam | **Med-Low** |
| 6 | **AMS security-policy state** | `dspmqspl -m <QM>` | `mq_security_policy_info` (info gauge), `mq_security_policy_key_reuse_count` | Yes — CLI-only | **Low** |

Sections 4.1–4.6 detail each.

## 4. Candidates in detail

### 4.1 FFST/FDC file count + oldest-age  — **High**

**State it exposes.** The presence, count, and age of First Failure Support
Technology (FFST) capture files — the lab's canonical "something is wrong at the
MQ-internals or configuration layer" signal.

**[data]** *"On IBM MQ for AIX or Linux systems, FFST information is recorded in
a file in the `/var/mqm/errors` directory."* Each file *"contains information
about an error that is normally severe, and possibly unrecoverable … either a
configuration problem with the system or an IBM MQ internal error,"* named
`AMQnnnnn.mm.FDC` where `nnnnn` is the reporting process ID and `mm` a sequence
number (`ffst-mq-aix-linux`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ffst-mq-aix-linux>).

**How to collect.** Filesystem-only — `stat` the `*.FDC` files under
`/var/mqm/errors` (and, per the lab's inventory, per-QM
`/var/mqm/qmgrs/<QM>/errors`; mapping recorded in
`docs/reports/2026-06-19-mq-json-logging-research.md`). Count files; take the
oldest `mtime` for the age gauge. No MQI connection required, so it reports even
when the QM is down.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_ffst_files` | gauge | `host`, `scope` (`system`\|`qmgr`), `qmgr` | Count of `*.FDC` files present now |
| `mq_ffst_oldest_age_seconds` | gauge | `host`, `scope`, `qmgr` | `now − mtime` of the oldest `*.FDC` (absent/`NaN` when count = 0) |

**Genuinely absent?** **[judgment]** Yes — filesystem-only; no MQI equivalent,
and not collected by the lab's cluster_* collectors.

**Priority rationale.** **[judgment]** Highest: cheapest possible collection
(stat one directory), strongest resiliency signal, and a natural **drive-to-zero
gauge** — a healthy lab holds these at 0, so any non-zero value is directly
alertable.

> **SEAM — event vs. metric (load-bearing).** The **appearance of a new FFST**
> is an *event* and belongs to the **separate future generic-event-handler
> epic**, not here. This component owns only the **count/age gauges** — the
> steady-state "how many FFSTs exist and how stale is the oldest" — not
> event emission, notification, or per-FDC parsing/decoding. Keeping the seam
> crisp: gauges here; the "a new FDC just landed" trigger there. (IBM notes each
> FFST also emits a `user.error` **syslog** record — *"When a process writes an
> FFST record, it also sends a record to syslog … at the user.error level"*
> (`ffst-mq-aix-linux`, cited above) — which is the natural hook for that other
> epic, reinforcing that the event path is separate from this metric path.)

### 4.2 In-doubt transactions (`dspmqtrn`) — **High**

**State it exposes.** Transactions the queue manager is holding **in-doubt** —
prepared but not yet resolved to commit/rollback — plus heuristically-completed
ones awaiting `xa-forget`. In a resiliency lab this is a first-class recovery
signal: in-doubt units of work stall after a failover/DR cutover or a broken
resource-manager connection and require operator/`rsvmqtrn` intervention.

**[data]** `dspmqtrn` *"display[s] details of transactions … coordinated by IBM
MQ and by an external transaction manager."* `-i` requests *"internally
coordinated, in-doubt XA transactions … for which the queue manager (TM) has
asked each resource manager (RM) to prepare to commit, but an error was reported
by one of the resource managers (for example, a network connection broke)"*;
`-e` requests *"externally coordinated, in-doubt XA transactions … for which the
queue manager (RM) has been asked to prepare to commit, but has not yet been
informed by the TM of the transaction outcome"*; `-h` covers heuristically
completed ones. Return code **102** is *"No transactions found"*
(`reference-dspmqtrn-display-incomplete-transactions`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqtrn-display-incomplete-transactions>).

**How to collect.** CLI-only — run `dspmqtrn -m <QM>` (optionally `-i`/`-e`
separately to split by coordination) and count the emitted `TranNum(...)`
records; RC 102 ⇒ 0.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_indoubt_transactions` | gauge | `qmgr`, `coordination` (`internal`\|`external`), `state` (`in_doubt`\|`heuristic`) | Count of transactions in that state now |

**Genuinely absent?** **[judgment]** Yes. `mq_prometheus` publishes
`ibmmq_queue_uncommitted_messages` (per-queue uncommitted message *count*), which
is a **different** thing from the number of **in-doubt units of work** at the QM;
the exporter emits no in-doubt-transaction count. CLI-only.

**Priority rationale.** **[judgment]** High: directly tied to the epic's
recovery/RPO theme — an in-doubt transaction is the textbook "silent stall after
failover" — and cheap to collect (one command, count lines). Slightly below FFST
only because it needs a running QM + an MQI-authorized invocation, whereas FFST
is a pure `stat`.

### 4.3 Config drift (`dmpmqcfg`) — **Med**

**State it exposes.** Whether a queue manager's live object configuration has
**drifted** from a known-good baseline — objects added/removed/altered out of
band relative to what the lab provisioned.

**[data]** *"Use the `dmpmqcfg` command to dump the configuration of an IBM MQ
queue manager."* The output is itself a set of `runmqsc` commands that recreate
the configuration (`reference-dmpmqcfg-dump-queue-manager-configuration`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dmpmqcfg-dump-queue-manager-configuration>).

**How to collect.** CLI-only — `dmpmqcfg -m <QM>`; count object definitions and
hash the normalized dump, comparing the hash against a committed baseline to
derive a 0/1 drift flag.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_config_objects` | gauge | `qmgr`, `type` (`queue`\|`channel`\|…) | Count of defined objects by type |
| `mq_config_drift` | gauge | `qmgr` | `1` if the normalized dump-hash differs from baseline, else `0` |
| `mq_config_dump_timestamp_seconds` | gauge | `qmgr` | Unix time of the last successful dump (freshness) |

**Genuinely absent?** **[judgment]** Yes — CLI-only; no MQI drift metric.

**Priority rationale.** **[judgment]** Medium: valuable for a "config is as
provisioned" invariant, but it needs a **baseline definition and a normalization
step** (ordering, timestamps, default-object noise via `-a`), which is real
design work, and drift is slow-moving (a periodic check, not a hot signal). Good
second-tier build once the collector skeleton from candidate 1/2 exists.

### 4.4 Install / patch inventory (`dspmqver`, `dspmqinst`) — **Med-Low**

**State it exposes.** The installed MQ version/fixpack/level, build type, license
type, and LTS-vs-CD release type per node — i.e. "is this box at the intended
9.4.x patch level, and what is it entitled to run."

**[data]** `dspmqver` reports, among its selectable fields, *Version, in the form
V.R.M.F*, *Level*, *Build type*, *License type*, and *Release type* (LTS/CD)
(`reference-dspmqver-display-version-information`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqver-display-version-information>).
`dspmqinst` *"display[s] installation entries from `mqinst.ini` and license
entitlement information"* and *"the license type (Production, Trial, Beta, or
Developer) and the licensed entitlement required"*
(`reference-dspmqinst-display-mq-installation`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqinst-display-mq-installation>).

**How to collect.** CLI-only — parse `dspmqver -f …` and `dspmqinst`. **[judgment]**
Note the lab already scrapes `dspmqver` into a *build-time* version manifest
(`ansible/gather-versions.yml`, `docs/specs/2026-06-18-version-manifest-design.md`);
this candidate would surface the same facts as a **runtime Prometheus
info-metric**, so a fleet-wide back-level node shows up on a board, not only in a
manifest artifact.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_installation_info` | gauge (constant `1`) | `host`, `version` (VRMF), `level`, `build_type`, `release_type` (`LTS`\|`CD`) | Info-metric; value always 1, facts in labels |
| `mq_installation_entitlement_info` | gauge (constant `1`) | `host`, `installation`, `license_type`, `entitlement` | Info-metric for license/entitlement |

**Genuinely absent?** **[judgment]** Yes as a *runtime metric* — `mq_prometheus`
emits no version/entitlement series. (`ibmmq_qmgr_*` includes a command-level but
not the packaged VRMF/entitlement.)

**Priority rationale.** **[judgment]** Med-low: real fleet-hygiene value
(drift-from-intended-patch), but it is slow-moving inventory, partly served
already by the build-time manifest, and info-metrics are low-urgency. Cheap to
add once the collector exists.

### 4.5 System error-log message rate (`AMQERR0*.LOG`) — **Med-Low**

**State it exposes.** The rate of MQ error/warning messages the queue manager
writes to its file-based system error log, by severity — a "MQ is complaining"
trend independent of any single event.

**[data / lab].** The lab treats the MQ system error log as **file-based, not
journald**: `/var/mqm/qmgrs/<QM>/errors/AMQERR0{1,2,3}.LOG` (and the QM-wide
`/var/mqm/errors/AMQERR0*.LOG`), inventoried in
`docs/reports/2026-06-19-mq-json-logging-research.md` and encoded as a tested
invariant (`tests/test_clusterboard.py`). Each `AMQ####` line carries a severity
suffix (`I`/`W`/`E`/`S`). IBM's diagnostic-file location page documents the same
error-directory model (`problems-location-telemetry-logs-error-logs-configuration-files`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=problems-location-telemetry-logs-error-logs-configuration-files>).

**How to collect.** Filesystem-only — tail/parse `AMQERR0*.LOG`, count new lines
by severity since last scrape → a monotonic counter.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_errorlog_messages_total` | counter | `qmgr`, `severity` (`I`\|`W`\|`E`\|`S`) | Cumulative AMQ log lines by severity |

**Genuinely absent?** **[judgment]** Partly — no MQI equivalent, so a *rate*
metric is genuinely new.

**Priority rationale + seam.** **[judgment]** Med-low, and flagged for a **seam
check**: parsing the error log for **specific AMQ codes / a new line appearing**
overlaps the **generic-event-handler epic** (same boundary as FFST §4.1) and,
for shipping the raw log, node/promtail-style log pipelines. Keep this candidate
strictly to a **severity-bucketed rate counter** (a metric), and defer any
code-specific alerting to the event-handler epic. Lower than config/inventory
until that seam is settled in #81.

### 4.6 AMS security-policy state (`dspmqspl`) — **Low**

**State it exposes.** The presence and shape of Advanced Message Security
policies per queue — quality of protection, algorithms, and key-reuse count.

**[data]** `dspmqspl` *"display[s] a list of all policies and details of a named
policy"* and the output *"shows the key reuse count for all policies"* along with
Quality of protection, signature/encryption algorithms, and signer/recipient DNs
(`reference-dspmqspl-display-security-policy`,
<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqspl-display-security-policy>).

**How to collect.** CLI-only — `dspmqspl -m <QM>`; parse policy blocks.

**Proposed metrics.**

| Metric | Type | Labels | Semantics |
|--------|------|--------|-----------|
| `mq_security_policy_info` | gauge (constant `1`) | `qmgr`, `policy`, `qop`, `sign_alg`, `encrypt_alg` | One series per defined policy |
| `mq_security_policy_key_reuse_count` | gauge | `qmgr`, `policy` | Configured key-reuse count |

**Genuinely absent?** **[judgment]** Yes — CLI-only.

**Priority rationale.** **[judgment]** Low: AMS is niche and the current lab arms
do not exercise message-level security, so this is near-zero signal today. Worth
recording as a candidate for completeness; build only if an AMS arm is added.

## 5. Considered and rejected (not candidates)

**[judgment]**

- **RDQM / Native HA / DRBD replication state** — already mapped by the lab's own
  `rdqmstate.py` / `nativehastate.py` collectors (§2). Not new work.
- **Bare QM run-state, listener status, connection counts** — covered by
  `ibmmq_qmgr_status` / `ibmmq_qmgr_active_listeners` / `ibmmq_qmgr_connection_count`
  and by the dspmq collector.
- **`/var/mqm` filesystem / log-extent disk usage** — the mount is node_exporter
  territory, and per-QM recovery-log sizing is already approximated by
  `ibmmq_qmgr_log_size_restart` / `…_reusable`. Would duplicate existing coverage.

## 6. Recommended first build (feeds #81)

**[judgment] Build candidate 1 — FFST/FDC count + oldest-age — first.** It is the
lowest-cost collector (a directory `stat`, no MQI connection, reports even with
the QM down), the highest-value resiliency signal (a clean drive-to-zero gauge
that alerts on any non-zero), and it establishes the **textfile-dropzone
collector pattern** the other candidates reuse. Crucially it forces the
**event-vs-metric seam** to be drawn explicitly at the start: this component
ships the *count/age gauges*; the *"a new FFST appeared"* event goes to the
separate generic-event-handler epic. Sequence the follow-on as: **(1) FFST
gauges → (2) in-doubt transactions → (3) config drift / install inventory**, with
error-log rate (§4.5) held until the event-handler seam is resolved in #81.

## 7. Sources

All IBM pages are **IBM MQ 9.4.x**, fetched + cached 2026-07-15 via
`tools/ibm_doc_cache.py` under `build/refs/ibm-docs/ibm-mq/9.4.x/<slug>/`
(gitignored; cite `content.txt` + `source_url`). The tool is committed; the
cached IBM content is not redistributed.

- **FFST: IBM MQ for AIX or Linux** (`ffst-mq-aix-linux`) — `/var/mqm/errors`,
  `AMQnnnnn.mm.FDC`, severe/unrecoverable, `user.error` syslog record:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ffst-mq-aix-linux>
- **dspmqtrn (display incomplete transactions)**
  (`reference-dspmqtrn-display-incomplete-transactions`) — `-i`/`-e`/`-h` in-doubt
  + heuristic transactions; RC 102 = none found:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqtrn-display-incomplete-transactions>
- **dmpmqcfg (dump queue manager configuration)**
  (`reference-dmpmqcfg-dump-queue-manager-configuration`) — dumps config as
  `runmqsc` commands:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dmpmqcfg-dump-queue-manager-configuration>
- **dspmqver (display version information)**
  (`reference-dspmqver-display-version-information`) — VRMF, level, build/license/release
  type fields:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqver-display-version-information>
- **dspmqinst (display IBM MQ installation)**
  (`reference-dspmqinst-display-mq-installation`) — `mqinst.ini` entries + license
  entitlement:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqinst-display-mq-installation>
- **dspmqspl (display security policy)**
  (`reference-dspmqspl-display-security-policy`) — policy list, QoP, key-reuse
  count:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-dspmqspl-display-security-policy>
- **Telemetry logs, error logs, and configuration files**
  (`problems-location-telemetry-logs-error-logs-configuration-files`) — MQ error
  directory model:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=problems-location-telemetry-logs-error-logs-configuration-files>
- **rdqmstatus (display RDQM status)**
  (`reference-rdqmstatus-display-rdqm-status`) — HA/DR status, node online/offline,
  failed resource actions (cited in §2 as *already-collected*):
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-rdqmstatus-display-rdqm-status>

### 7.1 In-repo references

- `ansible/roles/mq-exporter/` · `docs/plans/2026-06-14-mq-prometheus-exporter.md`
  · `docs/site/docs/guides/mq-metrics-config-guide.md` — the stock `mq_prometheus`
  deployment and its MQI-sourced `ibmmq_*` coverage (§1).
- `src/mqlab/nativehastate.py`, `rdqmstate.py`, `clusterstate.py` ·
  `docs/specs/2026-06-27-component-extraction-roadmap-design.md` §4.1 — the lab's
  existing non-MQI collectors and the future `mq-resiliency-observability` bundle
  (§2).
- `docs/reports/2026-06-19-mq-json-logging-research.md` ·
  `tests/test_clusterboard.py` — FFST/FDC and `AMQERR` file-path inventory and the
  file-based-log invariant (§4.1, §4.5).
- `ansible/gather-versions.yml` · `docs/specs/2026-06-18-version-manifest-design.md`
  — the existing build-time `dspmqver` manifest (§4.4).
</content>
</invoke>
