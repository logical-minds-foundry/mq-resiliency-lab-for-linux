# Lab Log Streaming — Live Tail into Grafana

> **Status:** design, approved in brainstorming 2026-06-12.
> **Date:** 2026-06-12
> **Author:** Phillip Moore (with Claude)
> **Context:** A bolt-on to the existing lab observability stack (Prometheus +
> Grafana on the `obs` VM). The metrics layer answers *"is it up, how loaded,
> is the network healthy."* It cannot answer *"what is actually happening right
> now"* — the message-by-message story of the firm app and the DTCC responder
> as they push trades through the HADR config, or the critical-event narration
> of the cluster daemons during a failover. This design adds **live log
> streaming** into the same Grafana surface so the operator can *watch* the lab
> behave, not just read its vitals.
>
> This is **observation machinery**, not production simulacra. The sim apps'
> MQ traffic is the signal we watch; their *log output* is a first-class
> product whose only customer is Grafana/Loki. We design the apps' logging
> around the integration, not the other way round.

---

## Contents

- [1. Goal & success criteria](#1-goal--success-criteria)
- [2. Approach (and what was rejected)](#2-approach-and-what-was-rejected)
- [3. Architecture & data flow](#3-architecture--data-flow)
- [4. The application log contract](#4-the-application-log-contract)
- [5. Capture: stdout → journald](#5-capture-stdout--journald)
- [6. Transport: Grafana Alloy → Loki](#6-transport-grafana-alloy--loki)
- [7. Display: the log-source catalog](#7-display-the-log-source-catalog)
- [8. CLI surface](#8-cli-surface)
- [9. Testing & validation](#9-testing--validation)
- [10. Scope boundary](#10-scope-boundary)
- [11. Open questions](#11-open-questions)

---

## 1. Goal & success criteria

Add the equivalent of a **remote `tail -f`** for chosen log sources on the lab
machines, rendered as **live-tailing panels** inside the existing Grafana
dashboard, bolted onto the current Prometheus/Grafana stack without disturbing
it.

**First increment:** the two simulator apps — the firm-side requester and the
DTCC-side responder — so the operator can watch messages send/ack in real time
during a test, including across an HADR failover.

**Success criteria.**

- During a test run, the firm and DTCC app messages appear in Grafana panels in
  **near-real-time** (Loki live-tail), reading naturally as a human log line.
- The operator can narrow the stream by **severity, host, and source**, and
  full-text-search the message (e.g. `|= "id=abc"`) to follow a specific trade —
  without the lab needing to understand trade semantics.
- Adding a further log source later (cluster daemons, MQ `AMQERR`, MQ-web) is a
  **one-line catalog entry**, not a refactor.
- Everything renders from topology as a pure function, unit-tested, and passes
  the existing `vrg-validate` gate.

This is the early, brought-forward delivery of what the lab design's
observability roadmap called "Plan D — timeline narration via a log
aggregator."

## 2. Approach (and what was rejected)

**Chosen: Loki + Grafana Alloy.** Apps print structured JSON to stdout →
journald → Alloy ships to Loki on `obs` → Grafana Logs panels live-tail. This is
the Grafana-native logs stack; Loki is the single new component, and the shipper
follows the same "download a binary + systemd unit + Ansible role" pattern as
`node-exporter`. The app stays a dumb emitter, and the pipeline generalizes to
*any* log on any node — including the cluster daemon logs we do **not** own.

**Rejected — app pushes straight to Loki's HTTP API.** Drops the shipper, but
couples our app code to Loki, loses journald's free metadata/timestamps, and —
fatally — does nothing for the Pacemaker/corosync/MQ logs we don't control. A
dead end for the broader vision.

**Rejected — Grafana Live + a custom websocket/panel.** Marginally more
"real-time," but means building and maintaining a custom panel plugin to
reinvent what Loki's Logs panel already does. Not worth it.

**Why not Prometheus for logs:** Prometheus indexes metrics, not log lines.
Pushing log text through it is a known anti-pattern (cardinality blow-up). Logs
need a log store; that store is Loki.

**Alloy, not Promtail:** as of early 2026 Promtail is in LTS and approaching
end-of-life; Grafana Alloy is the current, supported collector. For a fresh lab
build, Alloy is the correct choice.

## 3. Architecture & data flow

```
epn_requester / epn_responder
   │  print one JSON object per line to stdout
   ▼
systemd-run --unit=mqlab-requester --collect      ← launch wrapper (per app)
   │
   ▼
journald   (on app-client / dtcc-sim)              unit=mqlab-requester, _HOSTNAME
   │
   ▼
Grafana Alloy   (fleet-wide, one per node)         reads journald, attaches host label
   │  Loki push API (mgmt plane)
   ▼
Loki   (NEW, on obs VM, :3100)                     single-binary, filesystem store, ~24h
   │
   ▼
Grafana Logs panel   {unit="mqlab-requester"} | json    ← live tail
```

**New pieces:**

1. **`loki` Ansible role** — single-binary Loki on `obs`, filesystem storage,
   retention trimmed to the ephemeral lab's life (match Prometheus' 24h). Bound
   to the mgmt plane on `:3100`. Joins `site-obs.yml` (obs-only).
2. **Grafana Loki datasource** — provisioned alongside the existing Prometheus
   datasource (one more entry in the Grafana role's `datasource.yml.j2`).
3. **`alloy` Ansible role** — fleet-wide, mirroring `node-exporter`: download
   binary, systemd unit, rendered config that scrapes journald and ships to
   Loki at `obs`'s mgmt IP. Joins `observability.yml` so
   `mqlab obs instrument <setup>` installs it across a setup's guests.

**Label discipline (load-bearing for Loki health).** Alloy attaches only
**low-cardinality labels**: `host` (from inventory) and `unit` (from journald).
Every rich field lives *inside* the JSON line and is parsed at query time with
`| json` in LogQL. Labels are the index; JSON is the payload. This keeps Loki's
stream count bounded while still allowing arbitrary field filtering.

## 4. The application log contract

Keep the structured contract **generic and syslog-shaped**, not domain-specific.
The lab does not parse trade semantics — any proprietary metadata (trade ids,
sequence numbers, queue names, payload detail) lives **inside the message text**
and in the source identity (host + unit), exactly as it would in ordinary
syslog. We are building a log viewer, not a trade-aware parser.

Both apps emit **one JSON object per line** through a **single shared emitter**
(`mqlab/obslog.py`, deployed next to the clients), with only generic fields:

| field | syslog analogue | example | purpose |
|---|---|---|---|
| `ts` | timestamp | `2026-06-12T14:03:01.123Z` | app-side precision timestamp (RFC 3339, UTC) |
| `level` | severity | `info` / `warn` / `error` | panel coloring, severity filtering |
| `msg` | message | `sent trade #42 to DTCC.REQUEST (id=a1b2c3)` | the human line; carries any domain detail as free text |

Two further dimensions arrive **from the journald + Alloy labels**, not the JSON
line, mirroring syslog's hostname and tag:

- `host` — which node (syslog hostname), attached by Alloy from inventory.
- `unit` — which app/source (syslog tag), e.g. `mqlab-requester`.

**Design notes.**

- The Logs panel displays `msg`; `| json` extracts `level` for coloring.
- Domain questions ("where did trade abc go?") are answered by **full-text line
  filters** over `msg` (`|= "id=a1b2c3"`) and by `host` / `unit` selectors — not
  by bespoke structured fields baked into the lab. This keeps the contract
  reusable for any future log source with zero schema change.
- We own the apps, so the `msg` text says exactly what is worth watching — but
  what we *parse* stays generic.
- The emitter is deliberately tiny and dependency-free (stdlib `json` + a clock),
  unit-tested for schema shape and one-object-per-line output.

The existing DR ledgers (`~/dr-ledgers/*.jsonl`) are **not** repurposed as a log
source; they serve DR-state reconciliation. Log emission is a separate, purpose-
built side channel.

## 5. Capture: stdout → journald

The apps gain their observability value only when launched under a **stable
systemd unit**, so journald tags every line with a predictable `unit` label.

- Launches change from bare `vagrant ssh … python …` to wrapping the invocation
  in `systemd-run --unit=mqlab-<role> --collect …`.
- `--collect` garbage-collects the transient unit after the run exits; journald
  retains the emitted logs for its normal window.
- Unit names are defined **once** (a small helper / constant), not scattered:
  `mqlab-requester`, `mqlab-responder`. The dashboard selectors key off these
  exact names, so they are part of the contract.
- `lab/scripts/e2e-test.sh` is updated to use the wrapped launch, so the standard
  end-to-end test produces live, tailed logs with no extra operator steps.

This same journald path is what later swallows Pacemaker/corosync (and any
journald-logging daemon) for free — the cluster panel needs no new capture
mechanism, only a new Alloy journald match and a catalog entry.

## 6. Transport: Grafana Alloy → Loki

**Alloy** runs on every instrumented node (fleet-wide, like `node-exporter`).
Its rendered config:

- Reads the systemd journal.
- Relabels to attach `host` (from inventory) and keeps `unit`; drops other
  high-cardinality journal fields from the label set.
- Writes to Loki's push endpoint at `obs`'s mgmt IP, `:3100`.

**Loki** runs single-binary on `obs`:

- Filesystem storage (no object store; this is an ephemeral lab).
- Retention trimmed to ~24h to match Prometheus and the lab's life.
- Bound to the mgmt plane; not exposed beyond it.

If the Alloy config is templated from topology (e.g. to inject the Loki target
IP and any per-node journald matches), that render is a pure function and is
unit-tested like the scrape-target and dashboard renders.

## 7. Display: the log-source catalog

The dashboard renderer (`src/mqlab/dashboard.py`) gains a **catalog** of curated
log sources, mirroring the existing `NET_SECTIONS` pattern. Each entry renders to
one Grafana **Logs panel**; adding a source later is appending one entry.

```python
LOG_SOURCES = [
    {"title": "Firm app — requester", "selector": '{unit="mqlab-requester"}', "hosts": ["app-client"]},
    {"title": "DTCC app — responder",  "selector": '{unit="mqlab-responder"}', "hosts": ["dtcc-sim"]},
    # deferred — each a one-line addition later:
    # {"title": "Cluster · Pacemaker", "selector": '{unit=~"pacemaker.*|corosync.*", host=~"pcmk-.*"}'},
]
```

Each Logs panel is configured with:

- The **Loki datasource**.
- The LogQL **selector + `| json`**.
- **Live-tail enabled**.
- `msg` as the displayed field; level-based coloring from `level`.

Layout follows the operator's **top-down investigation path**. A new
**"Application messages" row** holds the two app panels and goes at the **very
top** of the dashboard. Full order, top to bottom:

1. **Application messages** — the new Logs row. Watch the app first: are
   messages flowing?
2. **MQ service** — the existing reserved middleware (queue-manager) row.
3. **VMs** — the existing PCMK / RDQM / standalone / observability rows.
4. **Networks** — the existing network sections, at the bottom.

You start at the application, drop to the queue manager, then dig down into hosts
and wires — increasing depth, decreasing abstraction. The renderer inserts the
Application-messages row at the top and pushes the existing rows down; it remains
a pure function `topo → dashboard JSON`, unit-tested in `test_dashboard.py`.
Selectors are topology-aware but hand-curated, exactly like the curated VM rows —
the cluster entry's `host=~"pcmk-.*"` derives from a topology group when added.

These app panels are **secondary** in visual weight for now and may be broken
into sub-panels later; the ordering above is the conceptual hierarchy, not a
final pixel layout.

## 8. CLI surface

Minimal — `mqlab obs dashboard` already re-renders, and `mqlab obs up` /
`instrument` already drive provisioning. Likely touches:

- `mqlab obs open` gains a Loki/Explore hint or a second deep-link, so the
  operator can jump straight to live tail.
- **Optional / maybe:** `mqlab obs logs <source>` as a convenience that prints
  the LogQL or an Explore URL for a catalog source. Flagged as a *maybe* — the
  dashboard is the primary surface; only add it if it earns its keep.

No new top-level command group; this rides on the existing `obs` group.

## 9. Testing & validation

- **Renderer tests** (`test_dashboard.py`): catalog → Logs-panel JSON, including
  the new row placement and per-source selector/datasource wiring.
- **Emitter tests** (`obslog.py`): schema shape, one JSON object per line, UTC
  timestamp format, no stray non-JSON output on stdout.
- **Config-render tests:** if the Alloy config is templated from topology, test
  that render the way `scrape.py` / `netstate.py` renders are tested.
- All under the single `vrg-validate` gate. Mind the known validation gotchas:
  100% branch coverage, `uv run pytest`, ruff magic-comma, `StrEnum`/UP042,
  never mask exit codes.

## 10. Scope boundary

**In this spec:**

- `loki` Ansible role on `obs`; Loki datasource in Grafana.
- `alloy` Ansible role, fleet-wide via `observability.yml`.
- `mqlab/obslog.py` shared structured emitter.
- Both sim apps converted to structured emission.
- `systemd-run` launch wrapping (incl. `e2e-test.sh`).
- The catalog renderer with the **two app panels** and the new row.
- Any CLI hint touches above.

**Explicitly deferred** (each proven a one-line catalog entry by the framework,
not built now):

- The three-node cluster Pacemaker/corosync log panel.
- MQ `AMQERR` / MQ-web log sources.
- Any log-derived metrics / Loki recording rules / alerting.

## 11. Open questions

- **Alloy config templating:** is the per-node Alloy config static (single Loki
  target, fixed journald match) or templated from topology? Lean static for the
  first increment unless a per-node match is needed; revisit when the cluster
  panel lands.
- **`mqlab obs logs` convenience command:** build it now or wait until the
  dashboard surface proves insufficient? Default: wait.
- **Trade payload / queue-manager name in the schema:** the contract omits the
  raw trade payload and the connection/QM name for now. Add if a concrete
  narration need appears.
