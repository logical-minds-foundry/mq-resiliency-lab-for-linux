# MQ Resiliency Lab for Linux

A reproducible, **nested-virtualization IBM MQ resiliency lab** for exercising
high-availability (HA) and disaster-recovery (DR) topologies end to end —
from the host machine down to running queue managers and a live message
path.

The whole lab is declared as code and rebuilt from scratch on demand: a
[Vergil](https://github.com/vergil-project) VM provides the host
environment, a single `topology.yaml` describes the guest fleet and its
networks, and Ansible provisions everything from OS to running queue managers.
Nothing is hand-built; everything is repeatable.

## What you can do here

- Form a 3-node HA group — **RDQM**, **Pacemaker/SAN**, or **Native HA** — with
  automatic failover, and prove a live message path survives it.
- Drive an asynchronous **DR / CRR** cutover to a matching 3-node group at a
  second site (3+3).
- Compare the three HA mechanisms — replicated-storage (RDQM), shared-SAN
  (Pacemaker), and log-replicated (Native HA) — on the same network fabric.

## Start here

- **[Getting Started](getting-started.md)** — build the environment and
  bring up your first running stack.
- **[Architecture](architecture/index.md)** — the layered walkthrough of how
  the whole lab fits together, with diagrams.
- **[Design & Specs](design-and-specs.md)** — how the lab was engineered.
