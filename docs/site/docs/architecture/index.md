# Architecture

This page walks the lab from the outside in: the host machine, the virtual
machines on it, the network fabric and node fleet inside the lab VM, and
finally the three MQ-service arms built on top. Everything shown here is
**as-built** and traces back to
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml).

## Layer 1 — The host and its VMs

The lab lives on an Apple-silicon Mac running Vergil identities. Each
identity has a `base` VM and may have a repo-scoped VM. The `vergil-user`
identity (where work happens) carries a `base` VM **and** a repo-scoped VM
for this project — that repo-scoped VM is the lab ("the big special one"),
hosting the entire MQ cluster. The `vergil-audit` identity provides
independent review. Isolating the lab in its own repo-scoped VM is what makes
it disposable and reproducible.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/01-host-and-vms.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Host and identity VMs"></iframe>

## Layer 2 — Inside the lab VM

Inside the lab VM the lab is itself virtualized — nested virtualization
(Apple silicon → macOS Virtualization → Lima → KVM/TCG) runs the guest
fleet. Those guests sit on a fabric of isolated libvirt networks: per-site
data and heartbeat networks, a WAN that links the two sites, the client/SVC
application networks, and the SAN networks. The networks are designed to be
**severable** so failures can be injected cleanly.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/02-inside-lab-vm.html" style="width:100%;height:520px;border:0;border-radius:8px;" title="Inside the lab VM"></iframe>

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

## Layer 3 — The standalone QM arm (the message path)

The simplest arm proves the message path itself: a single queue manager
(`qm-main`) exchanging messages with a simulated upstream (`svc-sim`) and an
application client over the client network. This is the foundation the HA/DR
arms build on.

> **A note on "service".** This lab uses *service* in two distinct senses. The
> **external service** (`SVC` / `svc-sim`) is the request/reply responder our
> queue manager exchanges messages with across the WAN — "service" in the
> web-service / REST-endpoint sense. Our own HA/DR queue manager is *not* called
> "the service": it is **the queue manager** (or "the broker") that serves
> connected MQ clients. Reserving the word "service" for the external responder
> avoids the ambiguity the two layers would otherwise create.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/04-standalone-qm.html" style="width:100%;height:320px;border:0;border-radius:8px;" title="Standalone QM message path"></iframe>

## Layer 3 — The Pacemaker/SAN arm (the HA contrast)

The Pacemaker arm provides the same single-site HA goal as RDQM but with a
different mechanism: shared SAN storage (`san-a`) fronted by a Pacemaker
cluster (`pcmk-a1..3`) instead of block-level replication. Running both on
the same fabric lets the lab compare replicated-storage HA against
shared-storage HA directly.

<!-- markdownlint-disable-next-line MD013 MD033 -->
<iframe class="diagram" src="diagrams/05-pacemaker-san.html" style="width:100%;height:360px;border:0;border-radius:8px;" title="Pacemaker/SAN arm"></iframe>
