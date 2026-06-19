# MQ JSON diagnostic logging — production infrastructure design

> **Issue:** #282. **Date:** 2026-06-19. **Scope:** MQ-general (all arms).
> **Status:** Design (approved 2026-06-19). **Depends on research:**
> [`docs/reports/2026-06-19-mq-json-logging-research.md`](../reports/2026-06-19-mq-json-logging-research.md).
> **Diagram:** [`diagrams/mq-json-logging-flow.html`](diagrams/mq-json-logging-flow.html).

## 1. Purpose & scope

Configure every IBM MQ diagnostic surface to **produce JSON-format diagnostic
logs** and make them **available and queryable** in the lab's log store, uniformly
across all arms (nativeha-rhel, pcmk, rdqm, …). This is the *production* half of
the logging story — getting MQ's diagnostics into a structured, line-oriented,
vendor-neutral form. How those logs are *consumed for display* is explicitly a
follow-on effort.

**The core deliverable is the boundary, not the dashboard:** once MQ diagnostics
are single-line JSON in the OS journal (plus one file for mqweb), they are
manageable by any tooling — `journalctl`, rsyslog forwarding, a SIEM, or the
lab's Alloy/Loki/Grafana stack. The transport stack is site-specific and
swappable; a client site can substitute its own collector without changing
anything MQ-side. See the flow diagram referenced above.

### In scope

- Queue-manager diagnostics (`qm.ini`).
- System/installation diagnostics (`mqs.ini`).
- Client application diagnostics (`mqclient.ini`).
- mqweb (MQ Console / REST) diagnostics (WebSphere Liberty).
- The minimal pipeline changes needed for **availability** (logs reach Loki and
  are queryable): one Alloy relabel rule and one Alloy file source.

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

All steps fail loud — no swallowed errors. Ansible tasks fail on write errors;
the verification step fails if a query returns empty.

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

A dedicated role owns the entire logging contract (every stanza template plus the
Alloy snippets) so it can be read and audited in one place. It exposes
surface-specific task files included with `tasks_from:` at each integration point:

| Task file | Included by | Action |
|---|---|---|
| `system.yml` | `mq-install` **and** `rdqm-install` (before `crtmqm`) | Write `mqs.ini` `DiagnosticMessagesTemplate` (inherited by each new QM at creation) + `DiagnosticSystemMessages`. |
| `qmgr.yml` | `mq-qmgr` | Idempotent `qm.ini` ensure-block (belt-and-suspenders for re-provisioned QMs). |
| `client.yml` | `mq-client` | `mqclient.ini` `DiagnosticSystemMessages`. |
| `web.yml` | `mqweb` | Add `<logging messageFormat="json" messageSource="message,ffdc"/>` to `mqwebuser.xml`. |

**MQ `.ini` format note.** MQ configuration files use a `Stanza:` header with
indented `key = value` lines (colon, not `[section]` brackets), and stanza names
can legitimately repeat. Ansible's `ini_file` module assumes bracketed INI and
cannot represent this; the implementation uses **`blockinfile`** with explicit
markers for idempotency.

### 4.2 `alloy` role changes (`config.alloy.j2`)

Two additions, both purely about making MQ logs *findable*:

1. **Relabel rule** mapping `__journal__syslog_identifier` → `unit`. MQ's
   syslog-sourced journal entries have no `_SYSTEMD_UNIT`, so without this they
   ship with an empty `unit` label and are effectively unqueryable. Mapping the
   identifier into `unit` makes MQ arrive as `unit="ibm-mq"` (and, conveniently,
   matches the cockpit's existing `unit=~".*mq.*"` filter for the follow-on
   dashboard work). Cardinality impact is negligible.
2. **`loki.source.file`** for `.../mqweb/logs/messages.log`, labelled
   `unit="ibm-mqweb"`, guarded so it is only configured on queue-manager nodes
   (which run mqweb).

JSON is parsed at **query time** in Grafana (`| json`), consistent with the
existing pipeline; ingest stays low-cardinality (labels `host`, `unit`).

## 5. Concrete configuration

```ini
# mqs.ini — seeded by mq-install / rdqm-install BEFORE crtmqm, so every new QM
# inherits the queue-manager stanza at creation.
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
# qm.ini — idempotent ensure-block (mirrors the inherited template); covers
# re-provisioned QMs that pre-date the template.
DiagnosticMessages:
   Name=ClusterSyslog
   Service=Syslog
   Ident=ibm-mq
   Severities=all
   ExcludeMessage=9001,9002
```

```ini
# mqclient.ini — client app diagnostics. NOTE: ExcludeMessage / SuppressMessage
# are not honoured in mqclient.ini, so the client surface is severity-filtered only.
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

**Severity policy.** `Severities=all` (Info and above) is deliberate: many
HA/CRR/Native-HA state-change events are emitted at Information severity, and an
errors-only filter would silently drop exactly the events that make the logs
interesting. `ExcludeMessage` trims known routine chatter; the list is a starting
point to be tuned against real drill output.

## 6. Cross-arm / OS-agnostic behaviour

`mq-diag-logging` only edits `/var/mqm/...` ini files and Liberty config, which
are identical on RHEL and Ubuntu, with identical paths — so the role is
OS-agnostic. Both install roles (`mq-install` for the Ubuntu arms, `rdqm-install`
for the RHEL arm) include `system.yml`; the shared `mq-qmgr`, `mqweb`,
`mq-client`, and `alloy` roles carry the rest. All arms are covered uniformly with
no per-arm logic.

**Correctness model.** Primary mechanism is **template inheritance at `crtmqm`**
(no QM restart needed; aligns with the cold-rebuild acceptance gate). The `qm.ini`
ensure-block exists for drift-correction and documentation; because MQ only reads
diagnostic stanza changes at QM start, any live change it makes would require a QM
restart — which on the HA arms must be done via cold rebuild, not mid-provision.
The implementation therefore treats the template path as authoritative and the
`qm.ini` block as a no-op on freshly built QMs.

## 7. Verification (availability acceptance)

Implemented as a verification step (CLI verb or test, decided in the plan), run
after provision + a fault drill:

1. **Journal shape:** `journalctl -t ibm-mq -o cat | head -1` parses as JSON.
2. **QM in Loki:** `{unit="ibm-mq"} | json | ibm_messageId != ""` returns rows.
3. **mqweb in Loki:** `{unit="ibm-mqweb"} | json` returns rows.
4. **Inheritance:** a freshly created QM's `qm.ini` contains the `DiagnosticMessages`
   stanza with no manual intervention.

## 8. Risks & spike-validate-first items

- **Core assumption:** MQ's `syslog()` output reaches journald with
  `SYSLOG_IDENTIFIER=ibm-mq`, and the journal `MESSAGE` field is the raw
  single-line JSON object. Verify on one arm before building out (Step 2 of #282).
- mqweb Liberty JSON uses its own `ibm_*` field set (distinct from MQ-core's) —
  still `| json`-parseable; the follow-on dashboard must account for both schemas.
- `ExcludeMessage` defaults are provisional; tune against observed drill volume.
- The always-on `AMQERRnn.LOG` text files remain on disk (cannot be disabled);
  they are neither shipped nor parsed, by decision.

## 9. References

- Research study: [`docs/reports/2026-06-19-mq-json-logging-research.md`](../reports/2026-06-19-mq-json-logging-research.md)
- Flow diagram: [`diagrams/mq-json-logging-flow.html`](diagrams/mq-json-logging-flow.html)
- Primary IBM 9.4 docs (cached under `build/refs/ibm-docs/ibm-mq/9.4/`):
  Diagnostic message services (`q018795`), Diagnostic message service stanzas
  (`q130440`), JSON format diagnostic messages (`q130430`), QMErrorLog stanza
  (`q019020`).
