# Architecture

This page walks the lab from the outside in: the host machine, the virtual
machines on it, the network fabric and node fleet inside the lab VM, and
finally the MQ-service arms built on top. The lab runs **three HA/DR
mechanisms** — RDQM (replicated-storage HA), Pacemaker/SAN (shared-storage HA),
and Native HA (log-replicated HA) — across **four arms** (Native HA runs on both
Ubuntu and RHEL), each a full **3+3** stack (a 3-node group in Data Center A with
an asynchronous DR/CRR relationship to a matching 3-node group in Data Center B),
all exchanging messages with a simulated cross-business counterparty (`svc-sim`)
over distributed queuing. Everything shown here is
**as-built** and traces back to
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml).

> **A note on "service".** This lab uses *service* in two distinct senses. The
> **external service** (`SVC` / `svc-sim`) is the request/reply counterparty our
> queue manager exchanges messages with across the inter-business network —
> "service" in the web-service / REST-endpoint sense. Our own HA/DR queue manager
> is *not* called "the service": it is **the queue manager** (or "the broker")
> that serves connected MQ clients. Reserving the word "service" for the external
> counterparty avoids the ambiguity the two layers would otherwise create.

## Layer 1 — The host and its VMs

The lab lives on a build host — an Apple-silicon Mac or an x86 Linux
machine — running Vergil identities. Each
identity has a `base` VM and may have a repo-scoped VM. The `vergil-user`
identity (where work happens) carries a `base` VM **and** a repo-scoped VM
for this project — that repo-scoped VM is the lab ("the big special one"),
hosting the entire MQ cluster. The `vergil-audit` identity provides
independent review. Isolating the lab in its own repo-scoped VM is what makes
it disposable and reproducible.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/01-host-and-vms.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Host and identity VMs"></iframe>

## Layer 2 — Inside the lab VM

Inside the lab VM the lab is itself virtualized — nested virtualization runs
the guest fleet under native KVM wherever the guest arch matches the host: on
the x86 Linux host every guest (including the x86_64 RHEL arms) is native KVM,
and on an Apple-silicon Mac (Apple silicon → macOS Virtualization → Lima) the
arm64 guests are native while only a foreign-arch guest — the x86_64 RHEL arms —
is TCG-emulated. On the Mac, guest RAM is backed by 2 MiB huge pages: with the
default 4 KiB pages, one memory-hungry guest made page faults in every nested
guest up to hundreds of times slower, and huge pages remove that cost
([#1240](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1240),
[#1241](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1241)).
`mqlab` picks this per platform from an environment profile it detects
automatically; the x86 host keeps 4 KiB pages. Those guests sit on a fabric of
isolated libvirt networks: per-site
data and heartbeat networks, a WAN that links the two sites, the inter-business
network to the counterparty (`net-ext`), the SAN networks, and a dedicated
management plane. The networks are designed to be **severable** so failures can
be injected cleanly. The **management plane** (`net-mgmt`) is "the Watcher": it
observes every node and carries Ansible control, but is deliberately non-transit
— nothing routes through it.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/02-inside-lab-vm.html" style="width:100%;height:620px;border:0;border-radius:8px;" title="Inside the lab VM"></iframe>

## Layer 3 — The RDQM arm (HA + DR building block)

The RDQM arm is the headline topology: a 3-node Replicated Data Queue
Manager HA group in Data Center A (synchronous replication, automatic
failover, RPO 0 inside the site) with an asynchronous DR relationship to a
matching 3-node group in Data Center B. HA never stretches across the WAN —
synchronous replication would tie every commit to inter-site latency and
turn a DC-to-DC partition into a cluster collapse; cross-site resilience is
always a separate, asynchronous DR relationship with a manual cutover.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/03-rdqm-arm.html" style="width:100%;height:480px;border:0;border-radius:8px;" title="RDQM HA/DR arm"></iframe>

## Layer 3 — The Pacemaker/SAN arm (the HA contrast)

The Pacemaker arm provides the same single-site HA goal as RDQM but with a
different mechanism: shared SAN storage (`san-a`) fronted by a Pacemaker
cluster (`pcmk-a1..3`) instead of block-level replication. Running both on
the same fabric lets the lab compare replicated-storage HA against
shared-storage HA directly. Like the other arms it is a full **3+3**: cross-site
resilience is an asynchronous DR peer in Data Center B (`pcmk-b1..3` fronting
`san-b`, whose LUN is DRBD-replicated from `san-a` over the WAN) that receives
the queue manager on a manual cutover — never a WAN-stretched cluster.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/05-pacemaker-san.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Pacemaker/SAN arm"></iframe>

## Layer 3 — The Native HA arm (log-replicated HA)

The Native HA arm reaches the same single-site HA goal a third way: MQ's
built-in **log replication** across 3 instances (raft — no shared storage, no
Pacemaker). One instance is the Active leader; failover is automatic. Crucially
it has **no VIP** — clients connect by a 3-instance CONNAME list and reconnect
to whichever instance is Active. Cross-site resilience is an asynchronous **CRR**
(Cross-Region Replication) relationship with a manual cutover. The lab runs it on
both an Ubuntu arm (`NHAUAPP`) and a RHEL arm (`NHARCAPP`).

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/06-native-ha-arm.html" style="width:100%;height:560px;border:0;border-radius:8px;" title="Native HA arm"></iframe>

## The admin plane — mqweb (data-plane infrastructure)

> **Status: off by default.** Per-node mqweb is gated by `mqweb_enabled`
> (`ansible/group_vars/all/admin.yml`, default `false`; #1171, #1188), so a default
> bootstrap starts no mqweb anywhere. It was turned off after mqweb failed to cold-start
> reliably on the 2-vCPU QM nodes under macOS/arm64 nested virtualization. Those failures
> predate the huge-page fix for arm64 page-fault cost (#1240, #1241), so they may not
> recur. The role is kept; set `mqweb_enabled: true` to run it deliberately. Whether and
> how mqweb comes back is undecided (tracked in the ad-hoc backlog, `.github#266`). The
> rest of this section describes the design when it is enabled.

When enabled, each queue manager runs **mqweb** — the MQ administrative REST API and
Console (WebSphere Liberty, `9443/HTTPS`) — as a stateless per-node service. It is
**data-plane infrastructure**: the control surface co-located with the queue
manager it fronts, part of what the lab *instruments*, **not** part of the
management/observability ("Watcher") plane that observes it. Clients reach it at
the queue manager's **data-plane** address — the floating VIP for the Pacemaker
and RDQM arms (each site, `vip` / `vip_b`), or the **active instance's node IP**
for the Native HA arms (which have no VIP; the active member is resolved at
runtime). The topology-derived source of truth for every queue manager's REST
endpoint is `mqlab rest render`.

## The event feed — MQ instrumentation events (every queue manager)

Every queue manager also runs the **`mq-event-monitor`** MQ SERVICE — an
`amqsevt` collector defined `CONTROL(QMGR)`, so it starts and stops **with** the
queue manager and travels with the active instance across HA failover. It
captures MQ instrumentation events (the standard event classes — authority,
connection, channel, queue-depth, command, configuration, and the rest) and
emits them as **`json_compact`** JSONL to journald under the tag **`mq-events`**
(a small launcher pipes the stream through `logger --size 32768` so long events
are not truncated). **Alloy** ships the journald stream to **Loki**, and Grafana
surfaces it alongside the node/MQ metrics — the *event* half of the Watcher's
picture, complementing the metrics half.

This is a **de-facto standard, not a single-QM proof of concept**: the shared
`mq-event-monitor` Ansible role configures it identically on **every** queue
manager — all four HA/DR arms (RDQM, Pacemaker/SAN, and both Native HA arms) plus
the shared `SVCQM` counterparty. Like mqweb, the collector is **data-plane
infrastructure co-located with the queue manager it instruments** — but its
output is what the observability plane consumes.

The collector's **sink is selectable** (`mq_event_sink`), resolved at
provisioning time so the delivered wrapper is single-purpose: **syslog** →
journald (the lab default, feeding Alloy → Loki as above), or a **file** (`.json`
event stream + `.error` diagnostics) for a site whose forwarding agent watches
files. Both are independently tested; the file variant carries a host-local
HA-failover stranding window the syslog sink does not, which is why syslog is the
lab default. See `docs/reports/2026-07-29-mq-event-monitor-file-sink-resilient.md`.

## The log-search tier — full-text over the log corpus (`logsearch`)

Alongside the Watcher's metrics + Loki logs sits a second telemetry tier:
**log search**, a single-node **OpenSearch + OpenSearch Dashboards + Data Prepper**
stack that gives the same log corpus a full-text, aggregation-capable search
surface. It runs **on the `obs` node** (`net-mgmt` `10.50.0.2`), next to
Prometheus, Grafana and Loki. It used to have its own `logsearch` node; that node
was folded into obs (#1179) so that one adequately sized instrumentation node,
with localhost links between its services, replaces two undersized ones. The
operator verbs are still `mqlab logsearch …`.

**Ingest path.** The tier does not add a second collector: **Alloy fans out the
*same* corpus Loki already receives.** Alloy has no native OpenSearch exporter (the
connector spike proved this), so the fan-out runs Alloy → **OTLP/gRPC** → **Data
Prepper** (on obs, port **`21892`**) → **OpenSearch**. The existing
**Loki path is untouched** — the fan-out is a *second* forward target, added for
parity so the search tier sees exactly the source set Loki does. The fleet-wide
fan-out is gated: the `observe` phase (and `mqlab commons up`) renders a gate file
(`build/work/logsearch/fanout.json`) that `group_vars/all/logsearch.yml` reads to
flip `alloy_fanout_opensearch` on across every host; without the gate file the
fan-out is cleanly **inert**.

**Indices.** Logs land in daily **`logs-YYYY.MM.DD`** indices under an index
template with **`number_of_replicas: 0`** — load-bearing on a single node, which can
never place a replica shard (the default `replicas: 1` would read *yellow* forever).
The template also turns off dynamic date detection (`date_detection: false`) and
maps MQ's free-form `ibm_commentInsert*` fields as text. Otherwise, the first insert
of the day that looked like a date fixed that field as `date`, and OpenSearch then
rejected the rest of the day's MQ JSON logs that carried text there (#1230;
[OpenSearch: date detection](https://docs.opensearch.org/3.8/mappings/#date-detection)).

**Nothing is dropped silently.** Data Prepper parses a body as JSON only when it
starts with `{` (`parse_when`, #1220), so plain journal lines are indexed unparsed
instead of flooding its log with parse errors. Any document OpenSearch still rejects
goes to a bounded dead-letter file on obs (#1239;
[Data Prepper DLQ](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/data-prepper-dlq.md)),
and Prometheus scrapes Data Prepper's sink counters so the Watcher's ③ Log pipeline
row and the `DataPrepperDocumentsRejected` alert show every rejection (#1238).

**Query surface.** Two ways in: **OpenSearch Dashboards** (`:5601`), and — for the
same panels as the rest of the fleet — a pinned **Grafana OpenSearch datasource**
(`uid: opensearch`, plaintext `:9200` on the same node) that sits alongside Grafana's
Prometheus and Loki datasources, so the log corpus is queryable from the obs Grafana too.

**Security (v1).** OpenSearch runs the **min (core-only) distribution** — zero
plugins, so there is **no security plugin at all** — serving plain http on
`:9200` (OpenSearch) / `:5601` (Dashboards), no auth. This is a deliberate v1 posture
for a **management-plane-only** service (spec §10); authentication is a follow-on.

**Persistence.** The obs node's guest disk is **ephemeral** (like every lab
guest), so durability is **host-side snapshot/restore only**: `mqlab logsearch
snapshot` takes a native OpenSearch `_snapshot` and fetches it to the host-durable
`build/state/logsearch/` bucket, and bring-up **auto-restores** the latest snapshot
(a fresh build with no snapshot is a logged no-op). There is no in-guest durable
store to protect.

**Lifecycle.** The tier is part of obs's bring-up (`site-obs.yml`), which starts it
**first**, before the metrics services: OpenSearch, then Data Prepper, then Dashboards,
each waiting for the one before to be ready. Started the other way round, the metrics
services' startup burst starved OpenSearch's cold start (#1194). It is reclaimed with
the rest of obs by `mqlab commons down` or the last stack's `teardown`.

## Where to next

This page is the **why** — the shape of the lab and the reasoning behind each
mechanism. For the **how** — running failover drills, cutting a stack over to its
DR site, driving the end-to-end request/reply path, and watching it all live —
see [Operate & Observe](../operate/index.md).
