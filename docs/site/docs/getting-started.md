# Getting Started

This page takes you from nothing to a running MQ stack. It stays at
orientation altitude and points to the real commands; per-arm operational
detail lives in the design spec and (later) the Operations section.

## 1. Build and enter the lab VM

The lab runs inside an ephemeral Vergil VM declared by
[`vergil.toml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/vergil.toml)
(the `[vm.vergil-user]` profile). It is 100% reproducible — rebuild it
freely.

```bash
vrg-vm create logical-minds-foundry/mq-cluster-tooling --identity vergil-user
vrg-vm session logical-minds-foundry/mq-cluster-tooling --identity vergil-user
```

All working state lives under the gitignored `build/` directory, mounted
from the host. Nothing else in the VM is precious.

## 2. Bring up the network fabric and a node set

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/main/lab/topology.yaml).
It defines the libvirt networks (data, heartbeat, WAN, client, DTCC, SAN)
and every guest's NICs and platform. The Vagrant/libvirt harness reads it to
create networks and boot nodes.

See the [Architecture](architecture/index.md) walkthrough for what each
network is for and why the fleet is shaped the way it is.

## 3. Stand up one stack end to end

The **standalone queue manager** path (Phase B) is the simplest proof that
the stack works: a single queue manager, a simulated upstream (`dtcc-sim`),
and an application client exchanging messages over the client network. Bring
it up, send a message, and confirm it survives a guest reboot.

From there, the [Architecture](architecture/index.md) page walks the
higher-order arms: the RDQM 3+3 HA/DR cluster and the Pacemaker/SAN
alternative.

## Building these docs locally

The site builds inside the project's docs container, the same way CI does:

```bash
# Stage the changelog + release-notes sources into the docs tree (once per
# session, or after CHANGELOG.md / releases/ change).
vrg-container-run -- vrg-docs-stage --docs-dir docs/site/docs

# Strict build (fails on any broken link or orphan page):
vrg-container-docs build --strict

# Or a live-reloading preview at http://localhost:8000 :
vrg-container-docs serve
```
