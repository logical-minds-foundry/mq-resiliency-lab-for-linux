# Operate & Observe

This page is the **how** — how to *run* the lab and *watch* it. It sits at
operational altitude: the commands you type against a live stack and the boards
you watch them on. For the **why** — the shape of the lab, the four arms, and the
reasoning behind each HA/DR mechanism — read [Architecture](../architecture/index.md)
first; everything below assumes that mental model. The code the lab runs on its
guests (the collectors, clients and benchmark) and the `mqlab component` verbs that
build and reinstall it are on [Guest components](guest-components.md).

Every verb here is stack-dispatched: you name a **stack** and `mqlab` resolves
the right mechanism-specific action from
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/lab/topology.yaml).
The four stack names are:

| Stack | Mechanism | OS | App queue manager |
|---|---|---|---|
| `rdqm-rhel` | RDQM (replicated-storage HA) | RHEL | `RDQMAPP` |
| `pcmk-ubuntu` | Pacemaker/SAN (shared-storage HA) | Ubuntu | `PCMKAPP` |
| `nativeha-rhel-crr` | Native HA (log-replicated HA) | RHEL | `NHARCAPP` |
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
`--from <phase>` / `--only <phase>` force or narrow it. `--no-dr` brings up the
**HA site only** — it skips the stack's DR-site guests (its `dr_groups`) and the
DR provisioning for a lighter footprint under load, and is stateless (re-run
without it to add DR over the live HA site). For the Pacemaker/SAN stack the
shared-storage peer stays up to keep its DRBD mirror intact, so `--no-dr` there
drops the DR *cluster* nodes rather than the whole of site B. Observability comes up
with `mqlab commons up` (it provisions the `obs` guest), so the Watcher is
already live before the first stack lands. Tear a stack down with
`mqlab teardown <stack>` (shared commons are reclaimed when the last stack exits).

`bootstrap`, `teardown` and `commons up` each print the **environment profile**
they resolved, for example `environment: macos (detected: …)`. The profile is
detected from the platform (Apple Virtualization → `macos`, Google Compute Engine
→ `cloud`, anything else → the base topology), and an explicit `MQLAB_ENV`
overrides it. On `macos` the guests' RAM is backed by 2 MiB huge pages, so
`bootstrap` reserves them on the host before booting anything (about 22 GiB for
`nativeha-ubuntu --no-dr`) and stops with `huge-page reservation SHORT` if the
host can't supply them. The last stack's `teardown` releases them. If you
override `MQLAB_ENV`, use the same value for `teardown` so the release still
happens.

Every bootstrap writes a **perf report**, `perf-<timestamp>.json`, beside its
transcript in `$(mqlab build path state)/runs/`, and prints a summary at the end.
To compare two runs (before and after a change, or macOS against cloud), use:

```bash
mqlab perf diff a.json b.json   # per-phase/milestone deltas + ratios, top steal contributors; no verdict
```

Reading the report and the one-lever-at-a-time tuning loop are covered in
[perf and staging](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/development/perf-and-staging.md).

### When a bring-up is slow or fails

Bring-up is built to either finish or stop loudly at a named step, never to hang:

- **VM boot.** Guests boot in batches of at most `boot_batch` (4 by default), and each
  batch's `vagrant up` is retried up to 3 times with backoff, so a transient
  management-NIC DHCP lease timeout does not end the run.
- **Readiness waits.** Each service the data path depends on has a bounded,
  fatal readiness wait. The log-search tier (OpenSearch, Data Prepper, Dashboards)
  waits up to 40 minutes each. A service the data path does not need (per-node mqweb,
  when enabled) only warns if it is slow. The waits are pinned by a guardrail test,
  so they cannot drift back to too-short values.
- **Package updates.** Baked Ubuntu boxes ship with apt auto-updates turned off. On
  any host that was not baked that way, provisioning masks the apt timers early, so a
  background update cannot hold the dpkg lock mid-run.

If a run does stop, fix the cause and resume from the failed phase with
`mqlab bootstrap <stack> --from <phase>` instead of starting over. The transcript and
perf report in `$(mqlab build path state)/runs/` show which step failed and how
long each phase took. A healthy cold `nativeha-ubuntu --no-dr` bootstrap takes
about 9–13 minutes on either platform, varying from run to run.

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
  Each publishes the resulting `lab_network_state` to the host node_exporter
  (`lab/scripts/net-state-publish.sh`, `sudo` for the drop-zone write), so the
  dashboard's network tiles follow the drill within one scrape. There is no
  polling probe: the lab's virtual networks only change when these scripts (or
  bring-up) change them (#1253).
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

## Inject WAN latency (`mqlab netem`)

The cross-region plane (`virbr-wan`) carries the CRR/DR replication traffic. To
study how a stack behaves under real inter-region distance — replication lag, the
strict-sync commit tax, switchover timing — `mqlab netem` injects a tunable,
**delay-only** latency on that plane and nothing else:

```bash
mqlab netem set --delay 10ms   # one-way delay on virbr-wan (RTT ≈ 2 × delay)
mqlab netem show               # show the current qdisc on each virbr-wan tap
mqlab netem clear              # remove all shaping, restore the default qdisc
```

The delay is applied symmetrically on **every guest tap enslaved to
`virbr-wan`**, so a one-way delay `D` yields a round-trip of `≈ 2D` in both
directions. The HA / heartbeat planes (`virbr-hb-a`, `virbr-hb-b`) are never
touched — intra-group Native HA replication is same-site and must stay unshaped.
`set` / `clear` / `show` shell out to `tc` / `ip` under `sudo` on the libvirt
host (they need `CAP_NET_ADMIN`).

### Measuring the sync tax with the benchmark client

`mq-bench` is the purpose-built instrument for the latency sweep. It is an entry
point of the baked `mq-resiliency-clients`
[guest component](guest-components.md) on `app-client`. It PUT+commits
persistent messages under syncpoint at a chosen offered rate and reports
commit-latency percentiles (p50/p95/p99/max) as one JSONL record per
`{mode, delay, msg_size, rate}` point, while emitting a node-exporter textfile for
the live dashboards.

`mq-bench` is a **Native HA** benchmark only
([#1380](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/1380)):
it measures the commit cost of Native HA replication (async CRR versus strict) on
`NHARCAPP` or `NHARIAPP`. Provisioning a Native HA stack renders the wrapper
`/home/vagrant/mq-bench` on `app-client`, with that stack's QM, CONNAME and TLS
defaults in front of the entry point. RDQM and Pacemaker stacks leave the wrapper
alone. Arguments you pass are appended after the defaults, so a later `--qm` or
`--offered-rate` wins:

```bash
# on app-client, after a Native HA stack is provisioned
./mq-bench --qm NHARCAPP --mode strict --delay-ms 10 --msg-size 2048 \
  --offered-rate 500 --warmup-seconds 30 --measure-seconds 120 --results bench.jsonl
```

When a run produces no commits, `mq-bench` reports the MQ reason codes it saw. It
says "QM unreachable" only when every failure was a connection failure; a 2035
`MQRC_NOT_AUTHORIZED`, for example, is reported as a refusal.

Pair it with `mqlab netem set` to quantify how injected WAN delay lands on the
commit critical path — the cost strict-sync replication puts on every commit
versus async CRR. It is deliberately separate from the always-on
`mq-app-requester` cockpit stream (which is "good-enough noise", not a
measurement tool).

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
mqlab obs net-state                   # render lab_network_state metrics from virsh (stdout; render-only)
mqlab obs reach-peers                 # render host→net→peer reachability
mqlab obs open                        # print the Grafana URLs + a Loki live-tail query
```

The render verbs are pure topology→file projections; the observe phase of
`mqlab commons up` runs them and hands the output to `site-obs.yml`, so a normal
bring-up leaves the boards current without a manual render.

### `mqlab logsearch` verbs

The **log-search** tier (single-node OpenSearch + Dashboards + Data Prepper; see
[Architecture](../architecture/index.md#the-log-search-tier-full-text-over-the-log-corpus-logsearch))
runs on the `obs` node. It comes up with obs (`mqlab commons up`, or a stack's
`observe` phase) and is reclaimed by `mqlab commons down`, so there is no separate
bring-up verb. Once it is up, `mqlab logsearch` is the operator surface over it:

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

#### Rejected log documents are loud, not lost

Data Prepper's OpenSearch sink can't index every line as-is, so the pipeline is
built to fail visibly:

- **Only JSON is parsed as JSON.** Data Prepper parses a log body only when it
  starts with `{` (`parse_when`). Plain journal lines pass through unparsed and are
  indexed as text, instead of each one logging a parse ERROR. A body that starts
  with `{` but still fails to parse is tagged `_jsonparsefailure` and counted.
- **No guessed date fields.** The `logs-*` index template sets
  `date_detection: false` and maps MQ's free-form `ibm_commentInsert*` fields as
  text, so an insert that happens to look like a date can no longer make the rest
  of the day's MQ JSON logs fail to index.
- **Rejected documents go to a dead-letter file.** Anything OpenSearch still
  rejects is appended to a bounded, rotated dead-letter file on obs instead of
  being dropped. Where it lives, how to read it, and how to replay it:
  [Data Prepper DLQ](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/data-prepper-dlq.md).
- **Rejections show on the Watcher and fire an alert.** Prometheus scrapes Data
  Prepper's sink counters. The Watcher's **③ Log pipeline** row charts rejections,
  and the `DataPrepperDocumentsRejected` alert fires on any new rejection
  (`DataPrepperSinkMetricsAbsent` fires if the counters disappear). The lab runs
  no Alertmanager, so alerts appear on Prometheus's Alerts page (`/alerts`) and the
  Watcher's log-pipeline alerts tile. Nothing is sent anywhere.

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
