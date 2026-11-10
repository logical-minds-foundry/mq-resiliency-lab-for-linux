# MQ Resiliency Lab for Linux

A lab that stands up **IBM MQ high-availability (HA) and disaster-recovery (DR)
topologies** end to end — from a bare x86 Linux host down to running queue
managers and a live message path that survives failover. You clone the repository
onto a virtualization-capable host; the lab brings everything else, and stands up
a full HA/DR stack with one command.

Full documentation lives in the site under [`docs/site/`](docs/site/docs/) —
this README is the on-ramp; the site is the depth.

## Table of Contents

- [What this is](#what-this-is)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Clone the repository](#clone-the-repository)
  - [Run it](#run-it)
- [Developing with Vergil](#developing-with-vergil)
- [Development](#development)

## What this is

At the core is **`mqlab`**, a standalone Python orchestrator, plus its support
tree — the Ansible roles that provision everything from OS to running queue
managers, the [`lab/topology.yaml`](lab/topology.yaml) fleet definition, and the
manifests. The deliverable is the **whole tree**, not a Python module: you run it
from a git checkout of the `develop` branch. There are no releases; `develop` is
the lab.

`mqlab` does the opposite of most tooling — rather than hiding the mechanics, it
**shows** them. Every command it runs (`virsh`, Ansible, `runmqsc`) is printed
verbatim as it runs and teed to a transcript, so you can watch a step, understand
it, then reproduce it by hand. The lab is transparent evidence, not a black box.

The author *develops* the lab inside a personal
[Vergil](https://github.com/vergil-project) dev VM, but Vergil only provides that
dev environment — strip it away and what remains is exactly `mqlab` and its
support tree, which is what you clone and run on your own host. See
[Developing with Vergil](#developing-with-vergil) below.

## Getting Started

This is the orientation-altitude version of the site's
[Getting Started](docs/site/docs/getting-started.md) — start there for the full
walkthrough.

### Prerequisites

Almost everything is fetched automatically — IBM **MQ Advanced for Developers**
(the no-charge edition) is pulled by the tooling, at the level pinned in
[`lab/mq-version`](lab/mq-version) (currently `10.0.1.0`). You provide:

- **An x86-64 Linux host with root/sudo** and nested virtualization — the lab
  creates nested libvirt/QEMU/Vagrant guests and wants a beefy box (roughly
  12 vCPU / 64 GiB). `mqlab doctor` reports anything missing.
- **[uv](https://docs.astral.sh/uv/) and Python 3.14** — the orchestrator's
  runtime (`uv` provisions its own pinned 3.14; the base OS Python is untouched).
- **libvirt / QEMU / Vagrant** — the virtualization stack the guests run on.
- **git** — to clone the lab.
- **A RHEL subscription** — only for the RHEL-based arms (RDQM, Native HA on
  RHEL). The canonical `pcmk-ubuntu` arm needs nothing extra. Both RHEL arms
  default to RHEL 10, which needs an x86-64-v3 CPU (`mqlab doctor` says
  whether yours is one); without it, build them on RHEL 9 instead.

Each stack's default and selectable OS versions (Ubuntu 24.04/26.04, RHEL 9/10)
live in one catalog, [`lab/versions.yaml`](lab/versions.yaml); the site's
[Getting Started](docs/site/docs/getting-started.md#choose-an-os-version) shows
how to pick one.

### Clone the repository

The lab runs from a checkout of the `develop` branch, the repository's only
branch. Clone it and enter the tree:

```bash
git clone https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux.git
cd mq-resiliency-lab-for-linux
```

To pick up later changes, `git pull` and re-run `./scripts/setup`.

### Run it

From the checkout, build the environment, pre-flight the host, then bring up your
first stack:

```bash
./scripts/setup                        # checks uv + Python 3.14, then runs `uv sync` to build the venv
uv run mqlab doctor                    # pre-flight the host: arch, KVM, libvirt, required tools
uv run mqlab bootstrap pcmk-ubuntu     # bring up a full HA/DR stack: net → vms → provision → observe
uv run mqlab status pcmk-ubuntu        # phase completion (✓/✗) for this stack
```

`./scripts/setup` sets up the **environment**, not the lab: it verifies `uv` and
a uv-managed Python 3.14 are present (installing 3.14 via `uv` if missing), runs
`uv sync` to materialize the virtual environment, and prints the next commands.
Run the CLI as `uv run mqlab …`: `uv run` puts the venv's `bin/` on `PATH`, so
the companion tools `mqlab` calls by bare name resolve. (Activating the venv with
`. .venv/bin/activate` works too, and then a bare `mqlab …` does.)
`uv run mqlab bootstrap pcmk-ubuntu` sequences the whole bring-up — the canonical
starting arm is **`pcmk-ubuntu`** (Pacemaker/SAN on Ubuntu, subscription-free and
x86-native) — and leaves you with a **running queue manager**. See
[Getting Started](docs/site/docs/getting-started.md) to send your first message
and watch it survive a failover.

## Developing with Vergil

The lab is *developed* inside an ephemeral [Vergil](https://github.com/vergil-project)
VM — the author's personal, solo-practitioner dev tooling — rather than on the
host directly. That is a development convenience, **not** part of the consumer
path above: strip Vergil away and what remains is exactly the tree you cloned.
It's my own tooling; if you'd find it useful, talk to me.

## Development

Developing the lab itself (as distinct from *running* it) uses the Vergil
workflow. Footprint and tooling are declared as the `[vm.vergil-user]` profile in
[`vergil.toml`](vergil.toml); build and enter the box with:

```bash
vrg-vm create logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
vrg-vm session logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
```

The box is ephemeral and fully reproducible, so don't hand-customize it.
Nothing in it outside the host-mounted `build/` directory is precious;
re-provision it freely to stay fresh:

```bash
vrg-vm rebuild logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
```

To change its tooling, edit the `[vm.vergil-user]` profile and rebuild; never
`apt install` inside a live VM.

Inside the VM, run the CLI as `uv run mqlab …` too, never the venv's `mqlab` by
its path: `uv run` puts the venv's `bin/` on `PATH`, so the tools `mqlab` calls
by bare name resolve (see
[`docs/development/operating-the-lab-from-a-dev-session.md`](docs/development/operating-the-lab-from-a-dev-session.md)).

See [`CLAUDE.md`](CLAUDE.md) for the workflow,
[`docs/site/docs/develop.md`](docs/site/docs/develop.md) for the developer
on-ramp, and
[`docs/development/perf-and-staging.md`](docs/development/perf-and-staging.md)
for comparing bootstrap perf reports across macOS and x86 cloud
(`mqlab perf diff`, the auto-detected `MQLAB_ENV` profiles, macOS huge pages, and
measured bootstrap times).
