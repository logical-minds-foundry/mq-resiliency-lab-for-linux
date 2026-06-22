# MQ Resiliency Lab for Linux

A reproducible, **nested-virtualization IBM MQ resiliency lab** for exercising
high-availability (HA) and disaster-recovery (DR) topologies end to end —
from the host machine down to running queue managers and a live message
path.

The whole lab is declared as code and rebuilt from scratch on demand: a
[Vergil](https://github.com/vergil-project) VM provides the host
environment, a single `topology.yaml` describes the guest fleet and its
networks, and Ansible plus a declarative content plane bring the MQ services
up. Nothing is hand-built; everything is repeatable.

## What you can do here

- Stand up a standalone queue manager and prove a message path survives a
  reboot.
- Form a 3-node **RDQM** HA group with synchronous replication and
  automatic failover, then drive an asynchronous DR cutover to a second
  site.
- Compare storage-replicated HA (RDQM) against shared-SAN HA (Pacemaker) on
  the same network fabric.

## Start here

- **[Getting Started](getting-started.md)** — build the environment and
  bring up your first running stack.
- **[Architecture](architecture/index.md)** — the layered walkthrough of how
  the whole lab fits together, with diagrams.
- **[Design & Specs](design-and-specs.md)** — how the lab was engineered.
