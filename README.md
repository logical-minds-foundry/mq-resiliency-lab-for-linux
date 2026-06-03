# mq-cluster-tooling

Tooling, scripts, and operating standards for running IBM MQ (MQ Series)
high-availability queue managers on Ubuntu Linux clusters.

> Status: bootstrapping. Design in progress under
> [`docs/specs/`](docs/specs/).

## Table of Contents

- [What this is](#what-this-is)
- [Development environment](#development-environment)

## What this is

A reproducible home-lab environment plus prototype tooling to stand up,
configure, and reliably fail over IBM MQ queue managers on Linux clusters.

The primary deliverable is the **design** — a validated HA/DR approach backed by
real evidence from running the lab — not the scripts themselves. The tooling is a
generically-written prototype that proves the concepts; it is not expected to
deploy as-is in a production environment. The lab is durable, reusable R&D
infrastructure: a test bed for replicating the deployment architecture and
experimenting with no real-hardware constraints.

## Development environment

This repo is developed inside an ephemeral, reproducible Vergil VM, not on the
host directly. Footprint and tooling are declared in
[`.vergil/vm-spec.toml`](.vergil/vm-spec.toml); build and enter the box with
`vrg-vm create --vm mq-lab` then
`vrg-vm session logical-minds-foundry/mq-cluster-tooling --vm mq-lab`. See
[`CLAUDE.md`](CLAUDE.md) for the workflow.

More detail will be filled in as the design solidifies.
