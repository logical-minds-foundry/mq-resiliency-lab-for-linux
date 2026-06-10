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
mqlab net up all       # define/start/autostart every lab network
mqlab net status       # which networks are defined / active / autostart
mqlab net show all     # per-network config + who is attached (DHCP leases)
mqlab net down all     # tear them all down
```

`up`, `down`, and `show` take a **selector** — a net name, a regex, or the
keyword `all` — so you can act on a subset:

```bash
mqlab net up data       # just net-data-a, net-data-b
mqlab net show net-wan  # one network
```

There is **no bare default**: `mqlab net down` with no selector is a usage
error, so a fat-finger cannot wipe the whole fabric — you must say `all`.

`mqlab net up all` runs the proven `lab/scripts/net-up.sh`, echoing each
`virsh net-define` / `net-start` / `net-autostart` so you see — and can copy —
exactly what brings the fabric up. Add `--step` to pause after each step and go
poke at the live system; break something and
`mqlab net down all && mqlab net up all` to rebuild, because the lab is a
disposable, reproducible illusion.

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/lab/topology.yaml)
— the libvirt networks (data, heartbeat, WAN, client, DTCC, SAN) and every
guest's NICs and platform. See the [Architecture](architecture/index.md)
walkthrough for what each network is for.

Once the fabric is up, bring up the guest VMs with **`mqlab vm`** — the same
selector paradigm, over `topology.yaml`:

```bash
mqlab vm status            # ground-truth fleet (via virsh), joined with topology
mqlab vm inventory         # render build/inventory.ini from topology — the static map
mqlab vm create rdqm       # create + provision the RDQM guests — watch Ansible run
mqlab vm create all --step # create every guest, pausing between each to poke around
mqlab vm up pcmk_san_ha    # start an existing setup's guests (virsh start)
mqlab vm down all          # shut them down  ( vm destroy all removes them + disks )
mqlab vm ssh rdqm-a1       # drop into a shell on one guest
```

Setups are named with **underscores** (`pcmk_san_ha`, `rdqm_dr`, …) so the same
token is the topology setup, the `mqlab` selector, **and** the Ansible inventory
group. `mqlab vm inventory` renders that inventory — every host with its static
management IP, the atomic role groups, and each setup as a group-of-groups —
straight from `topology.yaml`, no live probing.

The verbs mirror a domain's lifecycle on two axes — **create/destroy** (existence)
and **up/down** (power):

- `vm create` runs `vagrant up` per guest — the one verb that uses Vagrant, because
  it both **creates** the VM and **provisions** it (this is where you watch Ansible
  build each machine).
- `vm up` / `vm down` / `vm destroy` drive **`virsh`** (`start` / `shutdown` /
  `undefine`). **libvirt is the ground truth for state; Vagrant is used only for
  create** — so these work regardless of Vagrant's metadata.

Every verb is **state-aware and idempotent**: it first runs `virsh list --all`
(streamed verbatim, like every other step) to see the live state, then acts only
on the guests that need it. Re-running `vm create all` skips guests that already
exist; `vm up` skips those already running; `vm down` skips those already off;
`vm destroy` force-stops a running guest before removing it, and skips any that
are already gone. Guests it leaves alone get a one-line advisory note (`·`)
explaining why — so the output always traces the decision back to the probed
state. Running a verb twice is safe and converges on the same result.

Like `net`, the mutating verbs require a selector, so a bare `mqlab vm destroy`
cannot wipe every guest.

> **More verbs land as the slices ship.** `mqlab net` and `mqlab vm` are live;
> arm setup, HA/DR operations, and the `status` / `check` dashboard arrive in
> subsequent slices, each extending this walkthrough.

## 3. Stand up one stack end to end

The **standalone queue manager** path (Phase B) is the simplest proof that
the stack works: a single queue manager, a simulated upstream (`dtcc-sim`),
and an application client exchanging messages over the client network. Bring
it up, send a message, and confirm it survives a guest reboot.

From there, the [Architecture](architecture/index.md) page walks the
higher-order arms: the RDQM 3+3 HA/DR cluster and the Pacemaker/SAN
alternative.

## 4. Watch the lab live (observability)

A dedicated **`obs`** VM runs **Prometheus + Grafana**, scraping `node_exporter`
across the whole fleet over the host-only **`net-mgmt`** plane — the one network
fault drills never sever, so the dashboard stays live exactly when something
breaks. A second node, **`mon-probe`**, carries the data-net NICs for the MQ
client exporters that land in a later slice.

Bring the pair up and provision it — one verb, provisioned as code:

```bash
mqlab obs up        # render scrape targets + inventory, create obs + mon-probe, provision
mqlab obs status    # are the pair up? which targets are scraped, up/down?
mqlab obs targets   # render build/prometheus/targets/node.json from topology + echo it
mqlab obs open      # print the Grafana URL + the workstation tunnel recipe
```

`obs` is a guest **inside** the Vergil VM, so opening Grafana from your
workstation means forwarding a local port through the VM. `mqlab obs open`
prints the exact recipe; in short:

```bash
# on your workstation (macOS):
limactl list                                                  # find this repo's instance
ssh -F ~/.lima/<instance>/ssh.config -L 3000:10.50.0.2:3000 <host-alias>
# then browse:  http://localhost:3000/d/lab-fleet-node   (admin / admin)
```

The **Fleet — Node Health** dashboard shows a tile per node. Scrape targets are
rendered from **the full `topology.yaml`**, so *every* node is a target — a node
that isn't running simply shows **red (`up == 0`)** rather than vanishing. That
is deliberate: a missing thing you can see beats a missing thing you can't.

`obs up` brings up only the observer pair; to put host metrics on a running arm,
overlay the fleet role onto its group:

```bash
mqlab vm create pcmk_a                                  # boots the arm (arm64, KVM-fast)
cd ansible && uv run ansible-playbook observability.yml --limit pcmk_a
```

Those tiles flip from red to green as `node_exporter` starts — no re-render or
restart needed, because the nodes were already targets. Now watch a fault become
visible: `mqlab vm down pcmk-a2` turns that tile red within a scrape interval,
`mqlab vm up pcmk-a2` turns it green again. That live red↔green flip is the
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
