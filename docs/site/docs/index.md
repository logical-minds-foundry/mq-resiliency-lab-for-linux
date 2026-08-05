# MQ Resiliency Lab for Linux

A downloadable, signed lab that stands up **IBM MQ high-availability (HA) and
disaster-recovery (DR) topologies** end to end — from a bare x86 Linux host down
to running queue managers and a live message path that survives failover.

Download the signed release, verify it, and bring up a full HA/DR stack with a
single command:

```bash
mqlab bootstrap pcmk-ubuntu
```

That one command defines the lab networks, boots the guest fleet, provisions the
cluster and queue manager, and wires up observability — with every underlying
step (`virsh`, Ansible, `runmqsc`) printed verbatim as it runs. The lab is
transparent evidence, not a black box.

## Who it's for

Anyone who needs to *understand* — not just click through — how IBM MQ stays up
under failure: MQ administrators, SREs, and architects evaluating or
implementing HA/DR. You bring a virtualization-capable x86 host; the lab brings
everything else.

## What you can do here

- Form a 3-node HA group — **RDQM**, **Pacemaker/SAN**, or **Native HA** — with
  automatic failover, and prove a live message path survives it.
- Drive an asynchronous **DR / CRR** cutover to a matching 3-node group at a
  second site (a full 3+3 stack).
- Compare the three HA mechanisms — replicated-storage (RDQM), shared-SAN
  (Pacemaker), and log-replicated (Native HA) — on the same network fabric.

## What it actually is

The whole lab is declared as code and rebuilt from scratch on demand. At its
core is **`mqlab`**, a standalone Python orchestrator, plus its support tree —
the Ansible roles that provision everything from OS to running queue managers,
the [`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml)
fleet definition, and the release manifests. Nothing is hand-built; everything
is repeatable.

That is the entire product. The author *develops* the lab inside a personal
[Vergil](https://github.com/vergil-project) dev VM, but **Vergil only provides
that dev environment** — strip it away and what remains is `mqlab` and its
support tree, which is exactly what the signed release ships and what you run on
your own host. (If you *are* a Vergil user, see [Develop the lab](develop.md).)

## Start here

- **[Getting Started](getting-started.md)** — download, verify, and bring up
  your first running stack on your own host.
- **[Methodology](methodology.md)** — why the lab is built the way it is:
  mastering a technology by standing an entire bespoke mini-enterprise up from
  the ground up, virtually.
- **[Architecture](architecture/index.md)** — the layered walkthrough of how the
  whole lab fits together, with diagrams.
