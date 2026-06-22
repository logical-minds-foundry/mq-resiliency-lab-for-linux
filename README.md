# MQ Resiliency Lab for Linux

Tooling, lab, and operating standards for running IBM MQ high-availability
queue managers on Linux clusters — packaged as a downloadable, signed release
you can stand up end-to-end with one command.

## Table of Contents

- [What this is](#what-this-is)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [(Optional) Verify the download](#optional-verify-the-download)
  - [Run it](#run-it)
- [Development](#development)

## What this is

A reproducible lab plus the `mqlab` orchestrator that brings up, configures, and
fails over IBM MQ queue managers across HA/DR topologies. The deliverable is the
**whole tree** (orchestrator + Ansible + lab definitions + manifests), not a
Python module — you run it from the extracted release.

## Getting Started

### Prerequisites

Almost everything is fetched automatically — IBM **MQ Advanced for Developers**
(no-charge) is pulled by the tooling; the virtualization stack is checked by
`mqlab doctor`. You provide:

- **A virtualization-capable Linux/macOS host with root/sudo** — the lab creates
  nested libvirt/QEMU/Vagrant guests and wants a beefy box (~12 vCPU / 64 GiB,
  nested virtualization). `mqlab doctor` reports anything missing.
- **A RHEL box/subscription** — only for the RHEL-based arms (pcmk-rhel, RDQM).
  Ubuntu arms need nothing extra.

### (Optional) Verify the download

The trust root is the **fingerprint below plus a key fetched out-of-band** — not
the `RELEASE-KEY.asc` bundled in the tarball.

```bash
gpg --recv-keys 5BABD50A78EBF24D2410AE52ADF1A99B75D24E54     # or import the key from the release page over HTTPS
gpg --fingerprint 5BABD50A78EBF24D2410AE52ADF1A99B75D24E54   # cross-check it matches this README
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```

### Run it

```bash
./scripts/setup                         # checks prereqs, runs uv sync
uv run mqlab doctor                     # pre-flight the host
uv run mqlab bootstrap distributed-pcmk-ubuntu   # one of the lab setups; bring it up, sit back
```

#### What `scripts/setup` does

It sets up the **environment**, not the lab:

1. Checks `uv` and Python 3.12 are available (fails loud if not).
2. Runs `uv sync` to materialize the virtual environment.
3. Prints the next commands (`mqlab doctor`, `mqlab bootstrap <setup-name>`).

You can do these by hand instead. `mqlab bootstrap` (e.g. `distributed-pcmk-ubuntu`) then
sequences the lab bring-up: networks → guests (create + provision) → observability.

## Development

This repo is developed inside an ephemeral, reproducible Vergil VM, not on the
host directly. Footprint and tooling are declared as the `[vm.vergil-user]`
profile in `vergil.toml`; build and enter the box with
`vrg-vm create logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user`
then
`vrg-vm session logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user`.
See `CLAUDE.md` for the workflow and `docs/development/release-runbook.md` for
cutting a release.
