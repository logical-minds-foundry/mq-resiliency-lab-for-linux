# Getting Started

This page takes you from nothing to a running MQ stack. It stays at
orientation altitude and points to the real commands; per-arm operational
detail lives in the design spec and (later) the Operations section.

## 1. Build and enter the lab VM

The lab runs inside an ephemeral Vergil VM declared by
[`vergil.toml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/vergil.toml)
(the `[vm.vergil-user]` profile). It is 100% reproducible — rebuild it
freely.

```bash
vrg-vm create logical-minds-foundry/mq-cluster-tooling --identity vergil-user
vrg-vm session logical-minds-foundry/mq-cluster-tooling --identity vergil-user
```

All working state lives under the gitignored `build/` directory, mounted
from the host. Nothing else in the VM is precious.

## 2. Drive the lab with `mqlab`

The lab is driven by **`mqlab`**, an operator orchestrator that does the
opposite of most tooling: rather than hiding the mechanics, it **shows** them.
Every command it runs — `virsh`, Ansible, `runmqsc` — is printed verbatim as it
runs, streamed live, and teed to a transcript under `build/runs/`. You can watch
a step, understand it, then reproduce it by hand. (Why expose rather than
encapsulate? Because the deliverable is transparent evidence for the
RDQM-vs-Ubuntu comparison — see [design & specs](design-and-specs.md).)

Bring up the libvirt network fabric and watch it happen:

```bash
mqlab net up        # define, start, autostart every lab network
mqlab net status    # which networks are defined / active / autostart
mqlab net down      # tear them all down
```

`mqlab net up` runs the proven `lab/scripts/net-up.sh`, echoing each
`virsh net-define` / `net-start` / `net-autostart` so you see — and can copy —
exactly what brings the fabric up. Add `--step` to pause after each step and go
poke at the live system; break something and `mqlab net down && mqlab net up` to
rebuild, because the lab is a disposable, reproducible illusion.

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/lab/topology.yaml)
— the libvirt networks (data, heartbeat, WAN, client, DTCC, SAN) and every
guest's NICs and platform. See the [Architecture](architecture/index.md)
walkthrough for what each network is for.

> **More verbs land as the slices ship.** Today `mqlab net` is live; guest
> lifecycle (`mqlab vms`), arm setup, HA/DR operations, and the `status` /
> `check` dashboard arrive in subsequent slices, each extending this walkthrough.

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
