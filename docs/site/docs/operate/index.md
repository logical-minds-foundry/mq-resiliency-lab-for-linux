# Operate & Observe

This page is the **how** — how to *run* the lab and *watch* it. It sits at
operational altitude: the commands you type against a live stack and the boards
you watch them on. For the **why** — the shape of the lab, the four arms, and the
reasoning behind each HA/DR mechanism — read [Architecture](../architecture/index.md)
first; everything below assumes that mental model.

Every verb here is stack-dispatched: you name a **stack** and `mqlab` resolves
the right mechanism-specific action from
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml).
The four stack names are:

| Stack | Mechanism | OS | App queue manager |
|---|---|---|---|
| `rdqm-rhel` | RDQM (replicated-storage HA) | RHEL | `RDQMAPP` |
| `pcmk-ubuntu` | Pacemaker/SAN (shared-storage HA) | Ubuntu | `PCMKAPP` |
| `nativeha-rhel` | Native HA (log-replicated HA) | RHEL | `NHARAPP` |
| `nativeha-ubuntu` | Native HA (log-replicated HA) | Ubuntu | `NHAUAPP` |

Queue-manager names are derived from each stack's short token — no literal is
hardcoded (`{short}APP` / `{short}SVC`).

## Bring a stack up

```bash
mqlab doctor                      # host pre-flight (run first)
mqlab commons up                  # shared infra + the Watcher (obs/probe/svc/app/dns)
mqlab bootstrap <stack>           # net → vms → provision → observe, resumable
mqlab status                      # topology joined with live virsh state
```

`mqlab bootstrap` runs from the first unsatisfied phase, so a re-run resumes;
`--from <phase>` / `--only <phase>` force or narrow it. Observability comes up
with `mqlab commons up` (it provisions the `obs` guest), so the Watcher is
already live before the first stack lands. Tear a stack down with
`mqlab teardown <stack>` (shared commons are reclaimed when the last stack exits).

## The end-to-end request/reply path

The lab always has a live workload: a continuous request/reply stream between our
HA queue manager and the simulated cross-business counterparty (`svc-sim`). This
is the traffic a failover drill perturbs and the Watcher measures.

```text
app-client                     our HA queue manager            svc-sim
┌─────────────────┐  APP.SVRCONN  ┌──────────────┐  distributed  ┌──────────────────────┐
│ mq-app-requester │ ────────────▶ │  app QM      │   queuing     │ SVCQM                │
│                 │               │ (e.g. PCMKAPP)│ ────────────▶ │  <SHORT>.SVC.REQUEST │
│                 │ ◀──────────── │              │  (net-ext)    │        │             │
└─────────────────┘   APP.REPLY   └──────────────┘               │        ▼             │
                                                                 │ mq-svc-responder@… │
                                                                 └──────────────────────┘
```

- **Requester (Business A).** The `mq-app-requester` systemd unit on `app-client`
  drives an always-on request/reply stream (fixed-rate, `APP.SVRCONN`) at the app
  queue manager's **data-plane address** — the floating VIP on the RDQM and
  Pacemaker arms, or the active-instance CONNAME list on the Native HA arms (which
  have no VIP). It publishes a node-exporter round-trip metric
  (`app_roundtrip_total`, `app_roundtrip_failures_total`, `app_roundtrip_latency_ms`)
  and logs each round trip to journald → Loki.
- **Responder (Business B / SVC).** One shared `SVCQM` on `svc-sim` hosts each
  stack's own `<SHORT>.SVC.REQUEST` queue, serviced by a per-stack
  `mq-svc-responder@<SHORT>.SVC.REQUEST` instance (e.g.
  `mq-svc-responder@PCMK.SVC.REQUEST`). Per-stack queues keep the always-on
  streams independent — a stuck responder affects only its own stack.

To fire a bounded burst by hand (each request must round-trip or the script exits
non-zero):

```bash
# lab/scripts/e2e-test.sh [COUNT=5] [QM=PCMKAPP] [CONN=<conname-list>]
lab/scripts/e2e-test.sh 20 PCMKAPP
```

## Failover drills

The always-on requester is the probe: induce a failure on the active node and
watch the stream dip and recover while the queue manager fails over. The
controlled, stack-dispatched lever is `mqlab qm`:

```bash
mqlab qm status <stack>    # HA resource / instance state
mqlab qm down <stack>      # stop the active QM, HA intact — the controlled-failover trigger
mqlab qm up <stack>        # bring it back
```

What each verb runs is per-mechanism (all from the stack's topology `verbs:`):
Pacemaker disables/enables the `mq_group` resource (`pcs`), RDQM runs
`endmqm -w` / `strmqm`, and Native HA stops/starts the `mqmonitor@` unit so the
raft group elects a new leader.

For harder, host-side drills that a clean `qm down` cannot express, the lab ships
fault scripts (run on the host, not through `mqlab`):

- `lab/scripts/nativeha-fault-suite.sh <qm>` — the Native HA fault suite
  (hard `virsh destroy` of the active node, then assert the end state).
- `lab/scripts/net-down.sh <net…>` / `lab/scripts/net-up.sh <net…>` — sever and
  restore a lab network to inject a partition (the planes are designed severable).
- `lab/scripts/drbd-degrade.sh` — degrade DRBD replication for a forced-DR drill.

Watch any drill on the stack's **cockpit** board and the **lab-watcher** front
door (see [The Watcher](#the-watcher)).

## DR: cutover and failback

HA never stretches the WAN — cross-site resilience is always a *separate,
asynchronous* DR relationship with a **manual** cutover. The unified operator
verb is `mqlab dr`:

```bash
mqlab dr cutover  <stack>   # site A → B (a2b)
mqlab dr failback <stack>   # site B → A (b2a)
mqlab dr cutover  <stack> --rpo0-drill   # seed a persistent message, assert it survives the cut
```

`--rpo0-drill` proves **RPO-0** (no message loss): it seeds a persistent message
before the cut and asserts it is present at the peer afterward — the survival
assertion behind the DR framing.

!!! note "Per-arm DR coverage as-built"
    `mqlab dr cutover|failback` currently drives the **RDQM** cross-site flow and
    refuses other mechanisms with a clean error. The other arms cut over through
    their own as-built flows: the **Native HA** arms run their switchover playbook
    (`ansible/site-nativeha-switchover.yml` / `…-ubuntu-switchover.yml`, the
    topology `dr-cutover` / `dr-failback` verbs), and the **Pacemaker/SAN** arm
    uses `lab/scripts/pcmk-dr-cutover.sh`. Run `mqlab parity` to print the
    cross-arm capability matrix.

## The Watcher

The **Watcher** is the management/observability plane — the `net-mgmt` side of the
lab that observes every node but never carries transit traffic. It runs on the
`obs` guest and fuses two halves of the picture:

- **Metrics (Prometheus → Grafana).** `node-exporter` on every node for host
  health; a per-stack `mq_prometheus` exporter for MQ metrics. Scrape targets are
  rendered from topology.
- **Events + logs (Alloy → Loki → Grafana).** Every queue manager runs the
  `mq-event-monitor` MQ SERVICE — an `amqsevt` collector that emits MQ
  instrumentation events as JSONL to journald under the `mq-events` tag. **Alloy**
  ships the journald stream (relabelled to `unit="mq-events"`, `unit="ibm-mq"`,
  `unit="ibm-mqweb"`, plus the app round-trip stream) to **Loki**, and Grafana
  surfaces it alongside the metrics.

### `mqlab obs` verbs

```bash
mqlab obs targets [--stack <stack>]   # render Prometheus file_sd targets + exporter list
mqlab obs dashboard                   # render every Grafana board from topology
mqlab obs net-state                   # emit lab_network_state metrics from virsh (host-side)
mqlab obs reach-peers                 # render host→net→peer reachability
mqlab obs open                        # print the Grafana URLs + a Loki live-tail query
```

The render verbs are pure topology→file projections; the observe phase of
`mqlab commons up` runs them and hands the output to `site-obs.yml`, so a normal
bring-up leaves the boards current without a manual render.

### `mqlab logsearch` verbs

The optional **`logsearch`** tier (single-node OpenSearch + Dashboards + Data
Prepper — see [Architecture](../architecture/index.md#the-log-search-tier-full-text-over-the-log-corpus-logsearch))
is a sibling of `obs`: it comes up with `mqlab commons up` (after `obs`) and is
reclaimed by `mqlab commons down`, so there is no separate bring-up verb. Once it is
up, `mqlab logsearch` is the operator surface over it:

```bash
mqlab logsearch status            # cluster health + disk-used + read-only/full check
mqlab logsearch open              # print the OpenSearch Dashboards URL
mqlab logsearch snapshot          # take a snapshot -> host-durable build/state/logsearch/
mqlab logsearch restore [--snapshot <name>]   # restore latest (or a named) host snapshot
```

- **`status`** reports `_cluster/health`, per-node disk-used, and any read-only /
  flood-stage-full state. On this single-node, `replicas: 0` tier **both green and
  yellow are healthy** — only *red* or an unreachable node fails. A read-only / full
  store is surfaced **loudly** and fails the command (never a silent skip).
- **`open`** prints the Dashboards URL and its Discover deep-link — the mgmt-plane
  full-text investigation surface (v1: plain http, no auth).
- **`snapshot`** takes a native OpenSearch `_snapshot` (point-in-time consistent)
  and fetches it to the host-durable `build/state/logsearch/` bucket — the only
  durability the ephemeral-disk node has.
- **`restore`** stages a host snapshot artifact back to the guest and restores it;
  with no `--snapshot` it restores the latest. Bring-up auto-restores the latest;
  an explicit `restore` with nothing to restore is a loud error, not a no-op.

Because logsearch fans out the *same* corpus Loki receives, the search tier and the
Grafana/Loki live-tail are two views of one log stream — use Dashboards for
full-text and aggregation, Grafana Explore for live-tailing a drill.

### The boards

`mqlab obs open` prints the current URLs. Grafana is anonymous (no login):

- **`lab-watcher`** — *the front door.* One instrument-strip row per commons
  support host and one rollup row per stack (mechanism · Site A live · Site B DR ·
  flow), each drilling into that stack's cockpit.
- **Per-stack cluster cockpit** — the deep HA/DR view for one stack (QM owner,
  per-node health, replication state).
- **Per-stack messaging board** — the request/reply flow, driven by the
  `mq-app-requester` round-trip metric.
- **Per-QM boards** and the **`lab-fleet-node`** node-health board round out the
  set. On the **Native HA** arms the per-QM board carries a **log-health band**
  (extent reclaim, log-disk fill, media-image recency, collector freshness, and the
  recovery-log logger-event feed); reading it — including why the active instance
  and its replicas legitimately diverge — is the
  [Native HA log-lifecycle runbook](../guides/nativeha-log-lifecycle-guide.md).

For a partition or failover drill, keep `lab-watcher` open for the whole-lab
verdict and the stack cockpit open for the mechanism detail; use Grafana **Explore**
against the Loki datasource (query printed by `mqlab obs open`) to live-tail the
requester and event streams as the drill runs.
