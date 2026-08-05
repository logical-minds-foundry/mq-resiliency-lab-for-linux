# Alloy → OpenSearch Connector Spike — Findings (GATING)

**Date:** 2026-08-05 (spike executed; filename date matches the epic plan's Task 1 artifact name)
**Issue:** #826 (epic logical-minds-foundry/.github#149 — logsearch tier)
**Task:** Epic plan Task 1 — the GATING connector spike. De-risks the one load-bearing
assumption (can Alloy write to OpenSearch?) before any role/topology work is built on it.
Gates Tasks 9–10.

**Status:** DECIDED, proven end-to-end.

## Decision

**Alloy has NO native Elasticsearch/OpenSearch exporter. The committed connector is the
Data Prepper fallback (spec §6, path 2):**

```
journald ──► Alloy ──(OTLP/gRPC)──► OpenSearch Data Prepper ──► OpenSearch (logs-* daily indices)
             (loki.source.journal            (otel_logs_source →
              → otelcol.receiver.loki          opensearch sink)
              → otelcol.exporter.otlp)
```

A second pipeline component — **Data Prepper — lands on the `logsearch` node** (alongside the
node's Alloy is not required; see "Where Data Prepper runs" below). This is exactly the
guaranteed fallback the spec named up front, so the architecture does not dead-end.

## Evidence

Versions used (current stable at spike time): **Alloy 1.18.0**, **OpenSearch 3.8.0**,
**Data Prepper 2.16.0**, all Linux x86-64. Run directly on the dev VM (GCP x86 instance) —
no Docker available; OpenSearch and Data Prepper both run on OpenSearch's bundled JDK, Alloy
is a static binary.

### Step 1 — native path is ABSENT (definitive)

Two independent checks agree:

1. **Binary component inventory.** `strings` over the Alloy 1.18.0 binary yields these
   `otelcol.exporter.*` components and **no elasticsearch/opensearch**:
   `awss3, datadog, debug, faro, file, googlecloud, googlecloudpubsub, loadbalancing, loki,
   otlp, otlphttp, prometheus, splunkhec, syslog`.
   `grep -c otelcol.exporter.elasticsearch` → **0**.

2. **Live config load.** `alloy validate` on a config referencing the hypothesized component:

   ```
   Error: test-es-exporter.alloy:1:1: cannot find the definition of component name
   "otelcol.exporter.elasticsearch"
   ```

   Alloy embeds a *curated* subset of OpenTelemetry Collector components; the
   `elasticsearch` exporter (the OTel-native way to write the ES/OpenSearch `_bulk` API) is
   not in the Alloy build. There is therefore no single-collector native path, and no
   loki→OTel bridge helps because there is no OpenSearch-speaking exporter to terminate it.

### Step 2 — throwaway OpenSearch

Single-node OpenSearch 3.8.0, `plugins.security.disabled: true` (spike only),
`discovery.type: single-node`, loopback bind. `curl _cluster/health` → **green**.

### Step 3 — winning path proven end-to-end

The exact Alloy config block that ships journald lines to OpenSearch (via Data Prepper):

```alloy
loki.source.journal "system" {
  forward_to    = [otelcol.receiver.loki.bridge.receiver]
  relabel_rules = loki.relabel.journal.rules   // reuse the production role's relabels
  // production role forwards to loki.write; the fan-out adds this second forward_to target
}

otelcol.receiver.loki "bridge" {        // Loki log entries → OTLP logs (Alloy-native bridge)
  output { logs = [otelcol.exporter.otlp.dataprepper.input] }
}

otelcol.exporter.otlp "dataprepper" {   // OTLP/gRPC → Data Prepper otel_logs_source
  client {
    endpoint = "logsearch-mgmt-ip:21892"
    tls { insecure = true }             // mgmt-plane-only, no TLS in v1 (spec §10)
  }
}
```

Data Prepper pipeline (`otel_logs_source` → `opensearch` sink):

```yaml
logs-pipeline:
  source:
    otel_logs_source: { ssl: false, port: 21892 }
  sink:
    - opensearch:
        hosts: ["http://localhost:9200"]
        insecure: true
        index: "logs-%{yyyy.MM.dd}"     # daily indices, spec §6
```

**Result.** Injected 6 synthetic journald-shaped MQ-JSON lines (`logger -t logsearchspike826`,
carrying token `SPIKETOKENZULU`) — 3 before Alloy start, 3 while running:

- `curl 'localhost:9200/logs-*/_count?q=SPIKETOKENZULU'` → **`{"count":6}`**; all six distinct
  tokens (`alpha`…`foxtrot`) present, each count 1.
- Field-scoped search (`q=body:retry`) returns the doc. Docs land in daily index
  `logs-2026.08.05`.

This confirms both **historical read on startup** and **continuous tailing** flow through the
fallback into OpenSearch and are searchable. No pipeline errors in the Data Prepper log.

## Two findings that shape downstream tasks

1. **`number_of_replicas: 0` is load-bearing, empirically.** Data Prepper's `opensearch` sink
   creates `logs-*` with the ES/OpenSearch default `replicas: 1`, which reads **yellow**
   forever on a single node (`_cat/indices` showed `yellow … 1 rep`). This is exactly why the
   design pins `number_of_replicas: 0` via an index template (spec §6, plan Task 4 Step 3):
   the template must exist **before** the sink writes so health reads green honestly.

2. **The MQ JSON arrives as a string in `body`, not promoted fields.** A stored doc looks like:

   ```json
   {"time":"…","severityNumber":0,"body":"{\"ibm_datetime\":\"…\",\"severity\":\"error\",
    \"host\":\"qm1\",\"unit\":\"ibm-mq\",\"message\":\"… SPIKETOKENZULU …\"}",
    "log.attributes.host":"spike-host","log.attributes.unit":"logsearchspike826"}
   ```

   The loki labels (`host`, `unit`) become `log.attributes.*`; the whole journald payload lands
   verbatim in `body` as a string. Full-text search over `body` works (criterion #3), but
   **structured queries on `severity`/`ibm_datetime` need the JSON promoted to top-level
   fields.** The clean place to do that is a Data Prepper `parse_json` processor on `body`
   (Data Prepper's job, not Alloy's) — a concrete input to the Task 4 index-template mappings
   and worth a note for the Task 9 fan-out. v1 criterion #3 (full-text + a generic
   time-bucketed aggregation) is satisfiable without it; promoting fields is the quality-of-life
   improvement for structured investigation.

## Where Data Prepper runs

On the **`logsearch` node** (spec §6 fallback). It is a second ingestion component the box must
bake/run beside OpenSearch + Dashboards — a note for the box-bake (plan Task 7) and the
`site-logsearch.yml` play (Task 10): the fan-out endpoint Alloy targets is Data Prepper's
`otel_logs_source` (gRPC 21892), not OpenSearch's `9200`. Alloy on the MQ/obs nodes gains only
the `forward_to`/exporter block above; it does not talk to OpenSearch directly.

## Operational notes for the roles

- Data Prepper needs Java 11+. The `-linux-x64` tarball does **not** bundle a JDK (the
  `…-jdk` variant does, or reuse a JRE). The role should either pull the with-JDK distribution
  or provide a JAVA_HOME. (In the spike, OpenSearch's bundled JDK 25 ran Data Prepper 2.16.0
  fine, modulo harmless `sun.misc.Unsafe` deprecation warnings.)
- Data Prepper's launcher `exec`s bare `java`, so `JAVA_HOME/bin` must be on `PATH` (setting
  only `JAVA_HOME` is insufficient) — a packaging note for the role's systemd unit.
- Default OTLP source ports: traces 21890, metrics 21891, **logs 21892**. Use the logs source.
