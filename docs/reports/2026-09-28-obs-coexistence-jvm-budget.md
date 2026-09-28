# Obs-node JVM/Node coexistence budget (#1180, epic .github#267)

**Date:** 2026-09-28
**Task:** epic `logical-minds-foundry/.github#267` plan Task 3 (issue
`mq-resiliency-lab-for-linux#1180`).
**Scope:** size the JVM/Node heaps so the six observability services coexist on the
consolidated `obs` node without OOM, and record the concrete figures pinned by the
`tests/test_obs_coexistence_budgets.py` guardrail.

## Context

Epic #267 folds the logsearch tier (OpenSearch + OpenSearch Dashboards + Data Prepper)
onto the `obs` node, which grows to **4 vCPU / 10 GB** (spec §5.1). That single node then
runs six memory-significant services plus the OS:

- **OpenSearch** — JVM, the primary log store.
- **Data Prepper** — JVM, the Alloy→OTLP→OpenSearch connector.
- **OpenSearch Dashboards** — Node.js runtime.
- **Prometheus, Grafana, Loki** — Go services.
- **mq_prometheus exporter** — Go service.

Left unbounded, OpenSearch alone claims ~50 % of RAM (~5 GB) and the Dashboards Node
old-space heap is sized from total RAM, so the three runtimes would collectively overcommit
the 10 GB node. This note fixes explicit caps and shows they fit with margin.

## Budget

All figures in MB. Heap caps are the **configurable** figures this task sets; RSS estimates
add each runtime's non-heap footprint (JVM metaspace/threads/direct buffers; Node young-gen +
code + buffers) to project real resident memory.

| Component            | Runtime | Heap cap        | Est. total RSS | Where the cap lives                                   |
| -------------------- | ------- | --------------- | -------------- | ----------------------------------------------------- |
| OS + kernel / cache  | —       | —               | ~1024          | —                                                     |
| OpenSearch           | JVM     | `-Xmx` **2048** | ~3072          | `opensearch_heap: "2g"` → `jvm.options.d/heap.options`|
| Data Prepper         | JVM     | `-Xmx` **512**  | ~768           | `data_prepper_heap: "512m"` → unit `JAVA_OPTS`        |
| OpenSearch Dashboards| Node    | old-space **1024** | ~1536       | `opensearch_dashboards_max_old_space_mb` → `node.options` |
| Prometheus           | Go      | —               | ~512           | (Go runtime, self-managed)                            |
| Grafana              | Go      | —               | ~384           | (Go runtime, self-managed)                            |
| Loki                 | Go      | —               | ~512           | (Go runtime, self-managed)                            |
| mq_prometheus export | Go      | —               | ~128           | (Go runtime, self-managed)                            |
| **Subtotal**         |         | **3584 (heaps)**| **~7936**      |                                                       |
| **Headroom to 10240**|         |                 | **~2304 (≈22 %)** |                                                    |

**Rationale for each cap**

- **OpenSearch 2 g** — unchanged from the standalone tier. 2 g is the store's working floor;
  going lower risks its own GC pressure/OOM. The change here is *intent*: it is now an explicit
  coexistence cap that stops the ~50 %-of-RAM default from starving the co-located services.
- **Data Prepper 512 m** — reduced from the standalone **1 g**. Data Prepper is a lightweight
  OTLP→`parse_json`→OpenSearch forwarder; 512 m is ample for the lab's single pipeline and
  returns 512 m to the shared budget.
- **Dashboards Node old-space 1 g** — new cap. Node otherwise sizes old-space from total RAM.
  1 g is generous for a single-user lab Dashboards. Written to `config/node.options`, the
  canonical OSD Node-options file; the memory circuit-breaker (a percentage of
  `max-old-space-size`) tracks it.

The projected ~7.9 GB resident against a 10 GB node leaves ~2.3 GB (≈22 %) headroom — margin
for page cache, transient spikes, and the "combine, then iterate RAM up only if a cold rebuild
shows pressure" contract (spec §8). Estimates are engineering judgment (heap cap + typical
non-heap overhead per runtime), **not** measurements; the authoritative check is VAL #1177's
arm64 cold rebuild.

## Dashboards migration/timeout hardening (#249 deadlock)

Independent of node size, epic #249 observed Dashboards deadlocking its saved-objects
migration: its request to OpenSearch hit the default 30 s `opensearch.requestTimeout` mid
migration, half-created `.kibana_1`, and then hung forever. This task raises, in
`opensearch_dashboards.yml`:

| Key                          | Old (default) | New       | Why                                                        |
| ---------------------------- | ------------- | --------- | ---------------------------------------------------------- |
| `opensearch.requestTimeout`  | 30000 ms      | 120000 ms | the timeout that fired mid-migration — the direct fix      |
| `opensearch.shardTimeout`    | 30000 ms      | 120000 ms | so the server-side shard wait does not undercut the client |
| `migrations.scrollDuration`  | `15m`         | `30m`     | the migration read-scroll keepalive (the real budget knob) |

**Correctness note.** OpenSearch Dashboards is forked from Kibana 7.10.2 and has **no**
`migrations.retryAttempts` config key (that is a later-Kibana setting). OSD fatally rejects
unknown config keys, so the plan's "migration retry budget" is realized with the knob that
*does* exist — `migrations.scrollDuration` — plus the two timeout raises. `requestTimeout` is
the direct fix; `shardTimeout` + `scrollDuration` are belt-and-suspenders alongside the faster
OpenSearch responses the larger consolidated node yields.

Sources (verified 2026-09-28):
[Configuring OpenSearch Dashboards](https://docs.opensearch.org/latest/install-and-configure/configuring-dashboards/),
[OSD sample `opensearch_dashboards.yml`](https://github.com/opensearch-project/OpenSearch-Dashboards/blob/main/config/opensearch_dashboards.yml),
[OSD `config/node.options`](https://github.com/opensearch-project/OpenSearch-Dashboards/blob/main/config/node.options),
`src/core/server/saved_objects/saved_objects_config.ts` (migrations schema: `batchSize`,
`scrollDuration`, `pollInterval`, `skip`, `delete.*` — no `retryAttempts`).

## Guardrail

`tests/test_obs_coexistence_budgets.py` pins the above at `vrg-validate` time: each heap cap is
parsed from its role default and its rendered artifact, checked within a per-service band, and
the three caps + reserved OS/Go/non-heap overhead are asserted to fit 10 GB with a ≥1 GB margin;
`opensearch.requestTimeout` is asserted at/above 120000 ms. A future drift is caught here instead
of at the slow, expensive cold-rebuild acceptance gate (VAL #1177).
