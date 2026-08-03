# Getting Started

This page takes you from nothing to a running MQ stack. It stays at
orientation altitude and points to the real commands; per-arm operational
detail lives in the design spec and (later) the Operations section.

## 1. Build and enter the lab VM

The lab runs inside an ephemeral Vergil VM declared by
[`vergil.toml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/vergil.toml)
(the `[vm.vergil-user]` profile). It is 100% reproducible — rebuild it
freely.

```bash
vrg-vm create logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
vrg-vm session logical-minds-foundry/mq-resiliency-lab-for-linux --identity vergil-user
```

All working state lives under the gitignored `build/` directory, mounted
from the host. Nothing else in the VM is precious.

Guests boot **pre-baked, per-role box images** rather than installing everything
on every bring-up: MQ, the RDQM/observability stacks, and the OSS agents are
baked once into a golden box per role, so a bring-up only does the fast,
instance-specific configuration. That is why the walkthrough below is minutes,
not the better part of an hour. The baked boxes persist across a VM rebuild
(they live on the persistent data disk), so "rebuild" comes in tiers of very
different cost — see the
[box model & rebuild tiers](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/box-model.md)
developer note.

## 2. Drive the lab with `mqlab`

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

```bash
mqlab doctor                 # pre-flight the host (arch, KVM, required tools)
mqlab bootstrap pcmk-ubuntu  # net → vms → provision → observe, every step streamed
mqlab status                 # phase completion (✓/✗) per stack
mqlab status pcmk-ubuntu     # just this stack
```

Each phase is gated by a live "satisfied?" probe, so bootstrap is **state-aware
and resumable**: it starts from the first *unsatisfied* phase and a re-run picks
up exactly where a previous one stopped — nothing already done is redone. If a
step fails, bootstrap halts and prints the resume hint (`mqlab bootstrap <stack>
--from <phase>`). Add `--step` to pause after each step and go poke at the live
system; `--from <phase>` / `--only <phase>` force the phase selection by hand.

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

## 3. Stand up one stack end to end

The quickest proof that the stack works is to bootstrap one arm and exercise it:
a queue manager, a simulated upstream (`svc-sim`), and an application client
exchanging messages over the client network. **Native HA on Ubuntu** is the
lightest starting point (no SAN, and it boots native on the dev host):

```bash
mqlab bootstrap nativeha-ubuntu   # bring the whole arm up end to end
mqlab qm status  nativeha-ubuntu  # confirm the QM is up (dspmq -o nativeha)
```

Send a message, then stop the active instance and watch the standby take over —
the QM survives, because that is the whole point of the arm.

From there, the [Architecture](architecture/index.md) page walks the
higher-order arms: the RDQM 3+3 HA/DR cluster and the Pacemaker/SAN alternative.

## 4. Watch the lab live (observability)

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

`obs` is a guest **inside** the Vergil VM, but the forward is **automatic** — no
manual tunnel. Lima forwards the base VM's port 3000 to your Mac's
`localhost:3000`, and the `vergil-portforward` relay bridges that to the obs
guest, so you just browse:

```text
http://localhost:3000/d/lab-watcher   (anonymous — no login)
```

`mqlab obs open` prints the exact URLs (and re-heals the relay if a grafana
restart wedged it).

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
