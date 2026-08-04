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
is TCG-emulated. Those guests sit on a fabric of isolated libvirt networks: per-site
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
shared-storage HA directly.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/05-pacemaker-san.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Pacemaker/SAN arm"></iframe>

## Layer 3 — The Native HA arm (log-replicated HA)

The Native HA arm reaches the same single-site HA goal a third way: MQ's
built-in **log replication** across 3 instances (raft — no shared storage, no
Pacemaker). One instance is the Active leader; failover is automatic. Crucially
it has **no VIP** — clients connect by a 3-instance CONNAME list and reconnect
to whichever instance is Active. Cross-site resilience is an asynchronous **CRR**
(Cross-Region Replication) relationship with a manual cutover. The lab runs it on
both an Ubuntu arm (`NHAUAPP`) and a RHEL arm (`NHARAPP`).

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/06-native-ha-arm.html" style="width:100%;height:560px;border:0;border-radius:8px;" title="Native HA arm"></iframe>

## The admin plane — mqweb (data-plane infrastructure)

Every queue manager runs **mqweb** — the MQ administrative REST API and Console
(WebSphere Liberty, `9443/HTTPS`) — as a stateless per-node service. It is
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
