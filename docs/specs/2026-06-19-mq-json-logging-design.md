# MQ JSON diagnostic logging — production infrastructure design

> **Issue:** #282. **Date:** 2026-06-19. **Scope:** MQ-general (all arms).
> **Status:** Design — approved 2026-06-19; pushback review applied 2026-06-19.
> **Depends on research:**
> [`docs/reports/2026-06-19-mq-json-logging-research.md`](../reports/2026-06-19-mq-json-logging-research.md).
> **Diagram:** [`diagrams/mq-json-logging-flow.html`](diagrams/mq-json-logging-flow.html).
> **Portable config recipe (MQ-only):**
> [`../reference/mq-json-logging-config.md`](../reference/mq-json-logging-config.md).

## 1. Purpose & scope

Configure every IBM MQ diagnostic surface to **produce JSON-format diagnostic
logs** and make them **available and queryable** in the lab's log store, across all
arms (nativeha-rhel, pcmk, rdqm, …). This is the *production* half of the logging
story — getting MQ's diagnostics into a structured, line-oriented, vendor-neutral
form. How those logs are *consumed for display* is explicitly a follow-on effort.

**The core deliverable is the boundary, not the dashboard:** once MQ diagnostics
are single-line JSON in the OS journal (plus one file for mqweb), they are
manageable by any tooling — `journalctl`, rsyslog forwarding, a SIEM, or the
lab's Alloy/Loki/Grafana stack. The transport stack is site-specific and
swappable; a client site can substitute its own collector without changing
anything MQ-side. See the flow diagram referenced above.

### In scope

- Queue-manager diagnostics (`qm.ini`).
- System/installation diagnostics (`mqs.ini`).
- Client application diagnostics (`mqclient.ini`) — on arms that deploy an
  `mq-client` node (the distributed arms); the QM/system/mqweb surfaces apply on
  every arm.
- mqweb (MQ Console / REST) diagnostics (WebSphere Liberty).
- The minimal pipeline changes needed for **availability** (logs reach Loki and
  are queryable): one Alloy relabel rule, one Alloy file source, and a journald
  rate-limit drop-in so nothing is silently dropped.
- `logcli` on the obs node and a **human debugging runbook**
  (`docs/reference/mq-logging-debugging.md`) — the pipeline has enough stages that
  it must be supportable without the AI.

### Out of scope (named follow-ons)

- The dedicated **queue-manager log dashboard** (pulling these logs apart).
- The **QM application-status row** on the cluster cockpit (showing the QM's
  identity/name, not just where it runs).
- Any panels, queries, or Grafana board changes.

## 2. Success criteria

After a fresh provision and a fault drill, all of the following hold:

1. `journalctl -t ibm-mq -o cat | head -1` emits one **valid single-line JSON**
   object (asserted by piping through a JSON parser).
2. Loki returns structured rows for
   `{unit="ibm-mq"} | json | ibm_messageId != ""`.
3. mqweb's `messages.log` is JSON and Loki returns rows for
   `{unit="ibm-mqweb"} | json`.
4. **Cold-rebuild gate:** a fresh provision yields a queue manager whose `qm.ini`
   carries the inherited diagnostic stanza with **zero manual steps**.
5. **No silent loss:** `journalctl -t ibm-mq` shows no "Suppressed N messages"
   rate-limit marker during the drill window.

All steps fail loud — no swallowed errors. Ansible tasks fail on write errors;
the verification step fails if a query returns empty or a suppression marker appears.

## 3. Architecture & data flow

```
QM      (qm.ini  DiagnosticMessages=Syslog) ─┐
system  (mqs.ini DiagnosticSystemMessages)   ├─ syslog() ─→ journald ─┐
client  (mqclient.ini DiagnosticSystemMsgs)  ─┘  (SYSLOG_IDENTIFIER)   │
                                                                       ├─ Alloy ─→ Loki
mqweb   (Liberty messageFormat=json) ─→ messages.log ── tail ──────────┘   (host, unit)
```

The three MQ-core surfaces use the **Syslog** diagnostic message service, which
emits each message as a single-line JSON object via `syslog()`; on the lab's
systemd hosts, journald captures these with `SYSLOG_IDENTIFIER=ibm-mq` and no
`_SYSTEMD_UNIT`. mqweb is WebSphere Liberty (not the diagnostic-service stanzas),
so it writes single-line JSON to `messages.log`, which is tailed as a file.

**Ingestion decision (from research, Step 3):** Syslog → journald, reusing the
existing journald-only Alloy pipeline, rather than file-tailing the per-QM
`AMQERR0x.json`. mqweb is the single deliberate file-tail exception (its JSON is
single-line, so the multi-line-stanza objection that drove the JSON-only decision
does not apply).

## 4. Components

### 4.1 New role: `mq-diag-logging`

A dedicated role owns the **MQ-core** logging contract (the `mqs.ini`/`qm.ini`/
`mqclient.ini` stanza templates and the journald drop-in) so it can be read and
audited in one place. It exposes surface-specific task files included with
`tasks_from:`. The mqweb surface is the exception: because Liberty config is
template-rendered, its JSON logging lives in the `mqweb` role's own template
(`mqwebuser.xml.j2`), not a `mq-diag-logging` task file — editing rendered XML
post-hoc would be fragile.

**Wiring is at the QM-creation seams, not the install seams.** MQ is installed and
queue managers are created by *different roles on different arms*. Verified reality:
real `crtmqm` invocations live in **four** roles — `mq-qmgr`, `mq-pcmk-qmgr`,
`mq-nativeha`, `mq-nativeha-spike` — and the **RDQM arm creates its QM outside the
shared plays** (`rdqm-install` only installs; `rdqm-ha` only forms the group via
`rdqmadm`). Hooking the install layer alone would miss arms (notably nativeha-rhel,
which installs via `mq-nativeha/install-RedHat.yml`). So `system.yml` runs **before
`crtmqm`** in each crtmqm-running role, and the template-inheritance backbone makes
RDQM correct too: seed `mqs.ini` at install and the RDQM QM inherits whenever its
`crtmqm -sx` runs.

| Task file | Wired into (seam) | Action |
|---|---|---|
| `system.yml` | Before `crtmqm` in each crtmqm-running role — `mq-qmgr`, `mq-pcmk-qmgr`, `mq-nativeha`, `mq-nativeha-spike` — **and** at the end of `rdqm-install` (RDQM creates its QM via the bash script `lab/scripts/rdqm-qm-create.sh` (`crtmqm -sx`), so seeding `mqs.ini` at install time means that script's QM inherits the template; the script itself is not modified — no collision with the in-flight RDQM HA/DR work). | Write `mqs.ini` `DiagnosticMessagesTemplate` (inherited by each new QM at creation) + `DiagnosticSystemMessages`; install the journald rate-limit drop-in (§4.3). |
| *(no `qmgr.yml`)* | — | **Dropped during implementation — template-only.** `crtmqm` copies the `DiagnosticMessagesTemplate` into `qm.ini` and it's active on first start; a separate marked `qm.ini` ensure-block would add a **duplicate** `DiagnosticMessages` stanza on every fresh build *and* wouldn't take effect without a QM restart. The template is the single source for the QM surface (covers all arms, incl. pcmk's shared-LUN `qm.ini` and per-node Native HA, since each `crtmqm` inherits). |
| `client.yml` | `mq-client`. | `/var/mqm/mqclient.ini` `DiagnosticSystemMessages` (marker block — coexists with the existing KeepAlive `lineinfile` in `site-distributed-shared.yml`). |
| *(mqweb template)* | `mqweb` role's `templates/mqwebuser.xml.j2`, gated by `mqweb_json_logging`. | Add `<logging messageFormat="json" messageSource="message,ffdc"/>` so Liberty writes single-line JSON to `messages.log`. Not a `mq-diag-logging` task file (see above). |

**MQ `.ini` format note.** MQ configuration files use a `Stanza:` header with
indented `key = value` lines (colon, not `[section]` brackets), and stanza names
can legitimately repeat. Ansible's `ini_file` module assumes bracketed INI and
cannot represent this; the implementation uses **`blockinfile`** with explicit
markers for idempotency.

### 4.2 `alloy` role changes (`config.alloy.j2`)

Two additions, both purely about making MQ logs *findable*:

1. **Conditional relabel** that sets `unit` from `__journal__syslog_identifier`
   **only when `__journal__systemd_unit` is empty** — a single rule over
   `source_labels = ["__journal__systemd_unit", "__journal__syslog_identifier"]`
   with a regex matching the empty-unit case. This makes MQ's syslog-sourced
   entries arrive as `unit="ibm-mq"` **without** clobbering the `unit` label of
   systemd-unit-sourced entries (corosync/pacemaker), and without touching DRBD
   (which logs via the kernel identifier). Negligible cardinality impact.
2. **`loki.source.file`** for
   `/var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log`,
   labelled `unit="ibm-mqweb"`, guarded so it is only configured on
   queue-manager nodes (which run mqweb). Path casing (`Installation1`) matches
   the lab's `mqweb` role.

JSON is parsed at **query time** in Grafana (`| json`), consistent with the
existing pipeline; ingest stays low-cardinality (labels `host`, `unit`).

### 4.3 journald rate-limit drop-in (no silent loss)

`syslog()` lands in journald, which rate-limits per service by default
(`RateLimitIntervalSec=30s`, `RateLimitBurst=10000`) and **silently drops** beyond
that. With `Severities=all`, an event storm (channel cycling, a Native-HA election
storm, exporter connection churn) could trip it and lose the very events the logs
exist to capture — a silent failure the project forbids. The role installs an
Ansible-managed drop-in on MQ nodes disabling the limiter for the (ephemeral,
24h-retention) lab; verification asserts no suppression marker appears.

## 5. Concrete configuration

```ini
# mqs.ini — seeded as the first step of each QM-creating role, BEFORE crtmqm, so
# every new QM inherits the queue-manager stanza at creation.
DiagnosticMessagesTemplate:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
   ExcludeMessage=9001,9002          # routine channel start/stop chatter (tunable)
DiagnosticSystemMessages:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
```

```ini
# qm.ini — NOT written directly. crtmqm copies the template above into qm.ini as
# this DiagnosticMessages stanza, active on first start (shown for reference):
DiagnosticMessages:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
   ExcludeMessage=9001,9002
```

```ini
# /var/mqm/mqclient.ini — client app diagnostics, as a marked block (coexists with
# the existing TCP KeepAlive edit). NOTE: ExcludeMessage / SuppressMessage are not
# honoured in mqclient.ini, so the client surface is severity-filtered only.
DiagnosticSystemMessages:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
```

```xml
<!-- mqwebuser.xml — Liberty single-line JSON to messages.log (third-party-mandated XML) -->
<logging messageFormat="json" messageSource="message,ffdc"/>
```

```ini
# /etc/systemd/journald.conf.d/10-mq.conf — disable rate-limiting on MQ nodes
[Journal]
RateLimitIntervalSec=0
RateLimitBurst=0
```

**Severity policy.** `Severities=all` (Info and above) is deliberate: many
HA/CRR/Native-HA state-change events are emitted at Information severity, and an
errors-only filter would silently drop exactly the events that make the logs
interesting. `ExcludeMessage` trims known routine chatter; the list is a starting
point to be tuned against real drill output.

## 6. Cross-arm behaviour

The `mq-diag-logging` role's *logic* is OS-agnostic — it only edits `/var/mqm/...`
ini files, a journald drop-in, and Liberty config, all identical on RHEL and
Ubuntu with identical paths. What is **per-arm is the wiring**: the role is
included at each of the QM-creation seams listed in §4.1, because those seams
differ by arm. There is no per-arm *branching inside* the role; there is per-arm
*inclusion* of it.

**Native HA note.** `mqs.ini` is per-node, so `DiagnosticSystemMessages` and the
journald drop-in are applied on every HA node; the `DiagnosticMessagesTemplate`
must exist on whichever node runs `crtmqm`, and the resulting `qm.ini` stanza is
then carried with the replicated QM data to the other instances.

**Correctness model.** The **sole** mechanism for the QM surface is **template
inheritance at `crtmqm`**: `system.yml` seeds the `mqs.ini`
`DiagnosticMessagesTemplate` *before* each role's `crtmqm`, so the new QM's `qm.ini`
carries the `DiagnosticMessages` stanza and it is active on first start — no QM
restart, aligned with the cold-rebuild acceptance gate. There is intentionally no
separate `qm.ini` ensure-block: it would duplicate the inherited stanza and would
not activate without a restart. To change the policy on an existing QM, cold-rebuild
it (the lab's model).

## 7. Verification (availability acceptance)

Implemented as a verification step (CLI verb or test, decided in the plan), run
after provision + a fault drill:

1. **Journal shape:** `journalctl -t ibm-mq -o cat | head -1` parses as JSON.
2. **QM in Loki:** `{unit="ibm-mq"} | json | ibm_messageId != ""` returns rows.
3. **mqweb in Loki:** `{unit="ibm-mqweb"} | json` returns rows (from
   `/var/mqm/web/installations/Installation1/servers/mqweb/logs/messages.log`).
4. **Inheritance:** a freshly created QM's `qm.ini` contains the `DiagnosticMessages`
   stanza with no manual intervention.
5. **No silent loss:** no "Suppressed N messages" marker for the `ibm-mq`
   identifier in the journal over the drill window.

## 8. Phase 0 spike gate (do this first)

The design rests on one load-bearing premise that **must be proven on one QM
before any role build-out** (#282 Step 2). The implementation plan's first task is
this spike; everything else is *blocked-by* it.

Confirm, from real `journalctl -t ibm-mq -o json` output on one configured QM:

1. The Syslog service actually emits to the local socket journald reads (vs.
   requiring rsyslog) — MQ entries appear in the journal at all.
2. journald tags them `SYSLOG_IDENTIFIER=ibm-mq` (the configured `Ident`).
3. The `MESSAGE` field is the **whole single-line JSON object** (the research
   wording "added to syslog … starting with the msgID and inserts" leaves room
   for a positional/structured form instead of a JSON blob — verify it is a blob).
4. mqweb: `messageFormat=json` in `mqwebuser.xml` produces JSON `messages.log`
   on the bundled Liberty and does not disturb the existing TLS serving.

If any sub-assumption fails, revisit the ingestion decision (e.g., fall back to
file-tailing `AMQERR0x.json`) before building the role.

## 9. Residual risks & tuning

- mqweb Liberty JSON uses its own `ibm_*` field set (distinct from MQ-core's) —
  still `| json`-parseable; the follow-on dashboard must account for both schemas.
- `ExcludeMessage` defaults are provisional; tune against observed drill volume.
- The always-on `AMQERRnn.LOG` text files remain on disk (cannot be disabled);
  they are neither shipped nor parsed, by decision.

## 10. References

- Research study: [`docs/reports/2026-06-19-mq-json-logging-research.md`](../reports/2026-06-19-mq-json-logging-research.md)
- Portable MQ-only config recipe: [`docs/reference/mq-json-logging-config.md`](../reference/mq-json-logging-config.md)
- Flow diagram: [`diagrams/mq-json-logging-flow.html`](diagrams/mq-json-logging-flow.html)
- Primary IBM 9.4 docs (cached under `build/cache/refs/ibm-docs/ibm-mq/9.4/`):
  Diagnostic message services (`q018795`), Diagnostic message service stanzas
  (`q130440`), JSON format diagnostic messages (`q130430`), QMErrorLog stanza
  (`q019020`).
