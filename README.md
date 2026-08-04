# MQ Resiliency Lab for Linux

A downloadable, signed lab that stands up **IBM MQ high-availability (HA) and
disaster-recovery (DR) topologies** end to end — from a bare x86 Linux host down
to running queue managers and a live message path that survives failover. You
bring a virtualization-capable host; the lab brings everything else, and stands
up a full HA/DR stack with one command.

Full documentation lives in the site under [`docs/site/`](docs/site/docs/) —
this README is the on-ramp; the site is the depth.

## Table of Contents

- [What this is](#what-this-is)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Download and verify the release](#download-and-verify-the-release)
  - [Run it](#run-it)
- [Developing with Vergil](#developing-with-vergil)
- [Development](#development)

## What this is

At the core is **`mqlab`**, a standalone Python orchestrator, plus its support
tree — the Ansible roles that provision everything from OS to running queue
managers, the [`lab/topology.yaml`](lab/topology.yaml) fleet definition, and the
release manifests. The deliverable is the **whole tree**, not a Python module:
you run it from the extracted, signed release.

`mqlab` does the opposite of most tooling — rather than hiding the mechanics, it
**shows** them. Every command it runs (`virsh`, Ansible, `runmqsc`) is printed
verbatim as it runs and teed to a transcript, so you can watch a step, understand
it, then reproduce it by hand. The lab is transparent evidence, not a black box.

The author *develops* the lab inside a personal
[Vergil](https://github.com/vergil-project) dev VM, but Vergil only provides that
dev environment — strip it away and what remains is exactly `mqlab` and its
support tree, which is what the signed release ships and what you run on your own
host. See [Developing with Vergil](#developing-with-vergil) below.

## Getting Started

This is the orientation-altitude version of the site's
[Getting Started](docs/site/docs/getting-started.md) — start there for the full
walkthrough.

### Prerequisites

Almost everything is fetched automatically — IBM **MQ Advanced for Developers**
(the no-charge edition) is pulled by the tooling. You provide:

- **An x86-64 Linux host with root/sudo** and nested virtualization — the lab
  creates nested libvirt/QEMU/Vagrant guests and wants a beefy box (roughly
  12 vCPU / 64 GiB). `mqlab doctor` reports anything missing.
- **[uv](https://docs.astral.sh/uv/) and Python 3.12** — the orchestrator's
  runtime.
- **libvirt / QEMU / Vagrant** — the virtualization stack the guests run on.
- **gpg** — to verify the signed release below.
- **A RHEL subscription** — only for the RHEL-based arms (RDQM, Native HA on
  RHEL). The canonical `pcmk-ubuntu` arm needs nothing extra.

### Download and verify the release

The lab ships as a **signed tarball** from the
[Releases page](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/releases).
Download the tarball and the two checksum files (substitute the version you're
installing):

```bash
VERSION=vX.Y.Z
base=https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/releases/download/$VERSION
curl -LO $base/mq-resiliency-lab-for-linux-$VERSION.tar.gz
curl -LO $base/SHA256SUMS
curl -LO $base/SHA256SUMS.asc
```

Verify it **before** you unpack. The trust root is the **fingerprint below plus
a key fetched out-of-band** — not any key bundled in the tarball:

```bash
gpg --recv-keys 5BABD50A78EBF24D2410AE52ADF1A99B75D24E54     # or import the key from the release page over HTTPS
gpg --fingerprint 5BABD50A78EBF24D2410AE52ADF1A99B75D24E54   # cross-check it matches this README
gpg --verify SHA256SUMS.asc SHA256SUMS                       # the checksums are signed by that key
sha256sum -c SHA256SUMS                                      # the tarball matches the checksums
```

Once both checks pass, unpack the tarball and enter the tree:

```bash
tar xzf mq-resiliency-lab-for-linux-$VERSION.tar.gz
cd mq-resiliency-lab-for-linux-$VERSION
```

### Run it

From inside the extracted tree, build the environment, pre-flight the host, then
bring up your first stack:

```bash
./scripts/setup                 # checks uv + Python 3.12, then runs `uv sync` to build the venv
mqlab doctor                    # pre-flight the host: arch, KVM, libvirt, required tools
mqlab bootstrap pcmk-ubuntu     # bring up a full HA/DR stack: net → vms → provision → observe
mqlab status pcmk-ubuntu        # phase completion (✓/✗) for this stack
```

`./scripts/setup` sets up the **environment**, not the lab: it verifies `uv` and
Python 3.12 are present (failing loud if not), runs `uv sync` to materialize the
virtual environment, and prints the next commands. `mqlab bootstrap pcmk-ubuntu`
then sequences the whole bring-up — the canonical starting arm is **`pcmk-ubuntu`**
(Pacemaker/SAN on Ubuntu, subscription-free and x86-native) — and leaves you with
a **running queue manager**. See
[Getting Started](docs/site/docs/getting-started.md) to send your first message
and watch it survive a failover.

## Developing with Vergil

The lab is *developed* inside an ephemeral [Vergil](https://github.com/vergil-project)
VM — the author's personal, solo-practitioner dev tooling — rather than on the
host directly. That is a development convenience, **not** part of the consumer
path above: strip Vergil away and what remains is exactly the tree you unpacked.
It's my own tooling; if you'd find it useful, talk to me.

## Development

Developing the lab itself (as distinct from *running* it) uses the Vergil
workflow. Footprint and tooling are declared as the `[vm.vergil-user]` profile in
[`vergil.toml`](vergil.toml); build and enter the box with:

```bash
vrg-vm create logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
vrg-vm session logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
```

See [`CLAUDE.md`](CLAUDE.md) for the workflow,
[`docs/site/docs/develop.md`](docs/site/docs/develop.md) for the developer
on-ramp, and [`docs/development/release-runbook.md`](docs/development/release-runbook.md)
for cutting a release.
