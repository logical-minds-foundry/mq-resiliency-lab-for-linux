# Getting Started

This page takes you from nothing — a bare x86 Linux host — to a running MQ stack
you can fail over. It stays at orientation altitude and points to the real
commands; per-arm operational detail lives in the design spec and the
[Operate & Observe](operate/index.md) section.

!!! warning "Consumer path status"
    The consumer path is documented; end-to-end validation on a clean x86 host
    is tracked in
    [#910](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/910).

## 1. Prerequisites

You bring a virtualization-capable host and a short list of tools; the lab
brings everything else (IBM **MQ Advanced for Developers**, the no-charge
edition, is fetched automatically by the tooling).

- **An x86-64 Linux host with root/sudo** and nested virtualization — the lab
  creates nested libvirt/QEMU/Vagrant guests and wants a beefy box (roughly
  12 vCPU / 64 GiB). `mqlab doctor` reports anything missing.
- **[uv](https://docs.astral.sh/uv/)** and **Python 3.14** — the orchestrator's
  runtime. `uv` provisions and manages its own pinned 3.14 interpreter (the base
  OS Python is left untouched), so you do not install Python yourself.
- **libvirt / QEMU / Vagrant** — the virtualization stack the guests run on.
- **gpg** — to verify the signed release below.
- **A RHEL subscription** — only for the RHEL-based arms (RDQM, Native HA on
  RHEL). The canonical `pcmk-ubuntu` arm used throughout this page needs nothing
  extra.

## 2. Download and verify the release

The lab ships as a **signed tarball** from the project's
[Releases page](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/releases).
Each release carries four assets: the tarball, its detached signature, a
`SHA256SUMS`, and a signed `SHA256SUMS.asc`.

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
gpg --fingerprint 5BABD50A78EBF24D2410AE52ADF1A99B75D24E54   # cross-check it matches the README
gpg --verify SHA256SUMS.asc SHA256SUMS                       # the checksums are signed by that key
sha256sum -c SHA256SUMS                                      # the tarball matches the checksums
```

Once both checks pass, unpack the tarball and enter the tree:

```bash
tar xzf mq-resiliency-lab-for-linux-$VERSION.tar.gz
cd mq-resiliency-lab-for-linux-$VERSION
```

## 3. Set up and pre-flight the host

From inside the extracted tree, build the Python environment and pre-flight the
host:

```bash
./scripts/setup     # checks uv + Python 3.14, then runs `uv sync` to build the venv
mqlab doctor        # pre-flight the host: arch, KVM, libvirt, and required tools
```

`./scripts/setup` sets up the **environment**, not the lab: it verifies `uv` and
a uv-managed Python 3.14 are present — installing 3.14 via `uv` if missing —
runs `uv sync` to materialize the virtual environment, and prints the next
commands. `mqlab doctor` then confirms the host can actually run the guests
before you commit to a bring-up.

!!! note "After a Python-version bump"
    The pinned Python version lives in `.python-version`. If it changes (or you
    are upgrading from an older checkout), re-run `./scripts/setup` (or
    `uv sync`) to rebuild the `.venv` against the pinned interpreter — an
    existing venv is **not** rebuilt automatically.

## 4. Bring up your first stack

One command stands up a whole HA/DR stack. The canonical starting arm is
**`pcmk-ubuntu`** — Pacemaker/SAN on Ubuntu, subscription-free and x86-native:

```bash
mqlab bootstrap pcmk-ubuntu   # net → vms → provision → observe, every step streamed
mqlab status pcmk-ubuntu      # phase completion (✓/✗) for this stack
```

`bootstrap` runs four idempotent phases in order — define the lab networks, boot
the guest fleet, provision the cluster and queue manager, then wire up
observability — and leaves you with a **running queue manager**. Guests boot
pre-baked, per-role box images rather than installing MQ on every bring-up, which
is why this takes minutes rather than the better part of an hour.

### Your first message

With the stack up, prove the whole point of the lab — that a live message path
survives failover. Confirm the queue manager is up, then (via the
[Operate & Observe](operate/index.md) walkthrough) put a message, fail the active
node, and watch the queue manager refloat with the message intact:

```bash
mqlab qm status pcmk-ubuntu   # confirm the QM is Started
```

The deep dive below explains what each `mqlab` verb is doing under the hood.

??? note "Developing with Vergil? (that's basically just the author)"
    The lab is *developed* inside an ephemeral
    [Vergil](https://github.com/vergil-project) VM — the author's personal
    solo-practitioner dev tooling — rather than on the host directly. That is a
    development convenience, **not** part of the consumer path: strip Vergil away
    and what remains is exactly the tree you unpacked above. If you're a Vergil
    user, [Develop the lab](develop.md) covers the `vrg-vm` workflow.

## How `mqlab` drives the lab

The lab is driven by **`mqlab`**, an operator orchestrator that does the
opposite of most tooling: rather than hiding the mechanics, it **shows** them.
Every command it runs — `virsh`, Ansible, `runmqsc` — is printed verbatim as it
runs, streamed live, and teed to a transcript under `build/state/runs/`. You can
watch a step, understand it, then reproduce it by hand. (Why expose rather than
encapsulate? Because the deliverable is transparent evidence for the
RDQM-vs-Ubuntu comparison — see [design & specs](design-and-specs.md).)

### One command brings up a stack: `mqlab bootstrap`

A **stack** is one HA/DR arm — a mechanism on an OS. `mqlab bootstrap <stack>`
stands the whole thing up in one command, running four idempotent **phases** in
order:

| phase | what it does |
|-------|--------------|
| `net` | define + autostart + start every lab libvirt network |
| `vms` | `vagrant up` the stack's guests (plus the shared commons VMs) |
| `provision` | configure the cluster/storage and build the queue manager |
| `observe` | render + provision Prometheus/Grafana targets for the stack |

Each phase is gated by a live "satisfied?" probe, so bootstrap is **state-aware
and resumable**: it starts from the first *unsatisfied* phase and a re-run picks
up exactly where a previous one stopped — nothing already done is redone. If a
step fails, bootstrap halts and prints the resume hint (`mqlab bootstrap <stack>
--from <phase>`). Add `--step` to pause after each step and go poke at the live
system; `--from <phase>` / `--only <phase>` force the phase selection by hand.

**Lighter footprint with `--no-dr`.** Most day-to-day work doesn't need the DR
site — it matters only when you're actively testing cross-site failover. Under a
full lab load the DR (site-B) guests add enough replication and CPU pressure that
a stack's HA site can struggle to hold quorum. `mqlab bootstrap <stack> --no-dr`
brings up **only the HA site**: it skips the DR-site guests and the DR
provisioning, leaving a stack that forms HA and runs its workload on a fraction of
the footprint. Which guests are skipped is the stack's `dr_groups` in
[`topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml).
The flag is **stateless** — it shapes only that bring-up and never tears anything
down — so adding DR later is simply re-running `mqlab bootstrap <stack>` *without*
it, over the live HA site. A stack that declares no DR site to skip rejects
`--no-dr` rather than mis-provisioning.

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml)
— the libvirt networks (data, heartbeat, WAN, client, SVC, SAN), every guest's
NICs and platform, and the stack registry itself. See the
[Architecture](architecture/index.md) walkthrough for what each network is for.

The four stacks bootstrap knows about:

| stack | mechanism | OS |
|-------|-----------|-----|
| `pcmk-ubuntu` | Pacemaker/SAN (shared-LUN HA + cross-site DR) | Ubuntu |
| `rdqm-rhel` | RDQM (DRBD replicated HA/DR) | RHEL |
| `nativeha-rhel` | Native HA (raft-log replication) | RHEL |
| `nativeha-ubuntu` | Native HA (raft-log replication) | Ubuntu |

Two `mqlab vm` utilities render or reach the guests directly:

```bash
mqlab vm inventory     # render build/work/inventory.ini from topology — the static map
mqlab vm ssh pcmk-a1   # drop into a shell on one guest
```

When you're done, tear a stack back down:

```bash
mqlab teardown pcmk-ubuntu   # destroy the stack's guests + overlay disks
```

`teardown` reclaims the shared commons VMs (obs/probe/svc/app/infra) only when no
other stack is still up — pass `--commons` to force their removal, or leave them
for the next stack. Like bootstrap, it names a stack, so a bare `mqlab teardown`
cannot wipe everything by accident.

### Build the queue manager with `mqlab qm`

The `provision` phase already stands the queue manager up as part of bringing a
stack live — bootstrap leaves you with a running QM. **`mqlab qm`** is the
lifecycle layer *on top* of that: drive the QM without re-running a whole
bootstrap. It dispatches per stack, so the same verbs cover every mechanism
(Pacemaker `pcs`, RDQM, Native HA `systemctl`).

```bash
mqlab qm status pcmk-ubuntu   # pcs status resources — where mq_group is Started
mqlab qm down   pcmk-ubuntu   # stop the QM cluster-side (pcs resource disable)
mqlab qm up     pcmk-ubuntu   # start it again (pcs resource enable)
mqlab qm create pcmk-ubuntu   # (re)build the QM + its HA resource group
mqlab qm destroy pcmk-ubuntu  # remove the QM + its HA resources
```

For the Pacemaker/SAN stack, `qm create` runs the **`mq-pcmk-qmgr` Ansible
role**: the queue manager is created on the shared LUN, taught to every node
(`dspmqinf` → `addmqinf`), wrapped in a **disabled** systemd unit, and handed to
Pacemaker as the `mq_fs` → `mq_vip` → `mq_qm` resource group. `qm up` / `qm down`
go **through Pacemaker** — `pcs resource enable` / `disable mq_group` on the
cluster's first node. Because the systemd units are disabled, the cluster is the
*only* thing that starts the QM; you never `strmqm` it by hand.

The role is the point: the streamed, verbatim task output *is* the reproducible,
step-by-step HA procedure — the artifact a client re-implements under their own
automation. Watching it run is how you come to understand exactly how MQ HA is
built on Pacemaker/SAN.

> **Which verbs each arm supports** is a live matrix: `mqlab parity` prints it,
> and `mqlab --help` (or `mqlab <group> --help`) lists the full command surface —
> including cross-site DR (`mqlab dr cutover` / `failback`) and the shared
> observability stack (`mqlab commons`).

## Exercise a stack end to end

The quickest proof that a stack works is to bootstrap one arm and exercise it: a
queue manager, a simulated upstream (`svc-sim`), and an application client
exchanging messages over the client network. The canonical `pcmk-ubuntu` arm
above does this; an even lighter arm to try is **Native HA on Ubuntu** (no SAN,
and it boots native on an x86 dev host):

```bash
mqlab bootstrap nativeha-ubuntu   # bring the whole arm up end to end
mqlab qm status  nativeha-ubuntu  # confirm the QM is up (dspmq -o nativeha)
```

Send a message, then stop the active instance and watch the standby take over —
the QM survives, because that is the whole point of the arm.

From there, the [Architecture](architecture/index.md) page walks the
higher-order arms: the RDQM 3+3 HA/DR cluster and the Pacemaker/SAN alternative.

## Watch the lab live (observability)

A dedicated **`obs`** VM runs **Prometheus + Grafana**, scraping `node_exporter`
across the whole fleet over the host-only **`net-mgmt`** plane — the one network
fault drills never sever, so the dashboard stays live exactly when something
breaks. A second node, **`mon-probe`**, carries the data-net NICs for the MQ
client exporters.

Metrics are only half the picture. Every queue manager also ships **MQ
instrumentation events** — authority failures, channel start/stop, queue-depth
alarms, and the rest — as `json_compact` JSONL to journald (tag `mq-events`) via
the shared `mq-event-monitor` role, standard on **every** QM. **Alloy** ships that
stream to **Loki**, and Grafana surfaces the events alongside the metrics, so the
boards show both what the fleet is *doing* (metrics) and what MQ is *reporting*
(events).

The observability stack is stood up **as part of `mqlab bootstrap`** — its
`observe` phase renders the scrape targets and dashboards from topology and
provisions the obs pair plus this stack's exporters. To bring the shared
observability VMs up on their own (independently of any stack), use `mqlab
commons up`. A few renders and the front door are exposed directly:

```bash
mqlab obs open       # print the Grafana URL + how to reach it from your workstation
mqlab obs targets    # render the Prometheus file_sd targets from topology + echo them
mqlab obs dashboard  # render the Grafana dashboards (Watcher + per-stack cockpits)
```

`obs` is a guest inside the lab, reached on your host at `localhost:3000`:

```text
http://localhost:3000/d/lab-watcher   (anonymous — no login)
```

`mqlab obs open` prints the exact URLs (and re-heals the forward if a grafana
restart wedged it).

An **optional** second telemetry tier — **`logsearch`** (single-node OpenSearch +
Dashboards) — gives the same log corpus a full-text, aggregation search surface
alongside Grafana/Loki. It is a sibling of `obs` (comes up with `mqlab commons up`),
operated with `mqlab logsearch` (`status`/`open`/`snapshot`/`restore`); see
[Architecture](architecture/index.md#the-log-search-tier-full-text-over-the-log-corpus-logsearch)
for the tier and [Operate &amp; Observe](operate/index.md#mqlab-logsearch-verbs) for the verbs.

**The Watcher** (`lab-watcher`, #488) is the lab-state front door: a support-layer
instrument strip (DNS/obs/probe/svc/app) plus a live/DR rollup row per stack, each
drilling into its own cockpit board. Scrape targets are rendered from **the full
`topology.yaml`**, so *every* node is a target — a node that isn't running simply
shows **red (`up == 0`)** rather than vanishing. That is deliberate: a missing
thing you can see beats a missing thing you can't. The predecessor **Fleet — Node
Health** board (`lab-fleet-node`) still renders a tile per node, at `/d/lab-fleet-node`.

**mqweb is not the Watcher.** The MQ **admin REST API and Console** (`mqweb`,
`9443/HTTPS`) is a separate surface from the observability plane above. It is
**data-plane infrastructure** co-located with each queue manager — reached at the
QM's data-plane VIP (Pacemaker / RDQM) or the active instance's node IP (Native HA,
which has no VIP) — **not** on the management / Watcher plane. `mqlab rest render`
prints each queue manager's canonical REST endpoint(s) for both sites.

Because `bootstrap` already made every node a scrape target, a fault is something
you can *watch*. Kill a live cluster node the same way the lab exposes every
other step — by hand, with `virsh`:

```bash
virsh -c qemu:///system destroy lab_pcmk-a2   # yank a node out from under the cluster
```

That node's tile turns red within a scrape interval while Pacemaker refloats the
QM elsewhere; bring it back (`mqlab bootstrap pcmk-ubuntu`, which resumes at the
`vms` phase) and the tile turns green again. That live red↔green flip is the
point — a fault you can *watch*.
