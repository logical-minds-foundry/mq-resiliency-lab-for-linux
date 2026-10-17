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
| `rdqm-rhel` | RDQM (replicated-storage HA) | RHEL 10 (RHEL 9 selectable) | `RDQMAPP` |
| `pcmk-ubuntu` | Pacemaker/SAN (shared-storage HA) | Ubuntu 26.04, IBM-unsupported (24.04 selectable) | `PCMKAPP` |
| `nativeha-rhel-crr` | Native HA (log-replicated HA) | RHEL 10 (RHEL 9 selectable) | `NHARCAPP` |
| `nativeha-ubuntu` | Native HA (log-replicated HA) | Ubuntu 26.04, IBM-unsupported (24.04 selectable) | `NHAUAPP` |

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

The OS column above shows each stack's default first. To build a stack on another
supported OS, pass a build file: `mqlab bootstrap <stack> --config <file>`, where the
file holds, for example, `os: rhel:9`. Bootstrap records the OS in the stack's
instance record (`build/state/instances/<stack>.json`). A later request for a
different OS is refused until `mqlab teardown <stack>`, and a running stack with no
record is refused by every stack command except `teardown`. See
[Getting Started](../getting-started.md#choose-an-os-version) for the rules and the
IBM-unsupported Ubuntu 26.04 warning. Bootstrap warns separately when the shared MQ
nodes (`svc-sim`, `app-client`, `mon-probe`: the catalog's `infra_mq`) run an
IBM-unsupported OS. Today that is Ubuntu 26.04, so every stack's bootstrap prints
`WARNING: the shared MQ nodes (infra_mq) run ubuntu:26, which IBM does not support
for MQ (…)`, RHEL stacks and already-running commons included.

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
  waits up to 40 minutes each. A service the data path does not need (per-node mqweb)
  only warns if it is slow. The waits are pinned by a guardrail test,
  so they cannot drift back to too-short values.
- **Package updates.** Baked Ubuntu boxes ship with apt auto-updates turned off. On
  any host that was not baked that way, provisioning masks the apt timers early, so a
  background update cannot hold the dpkg lock mid-run.

- **Host CPU starvation.** If nodes fence or Native HA fails over with nothing driving
  the lab, check the host before the lab: `mqlab vm starvation` fails loud when the
  guests' kernels show they were not being scheduled. On macOS, that means other VMs or
  apps are using the Mac's CPU. See
  [the lab-gotchas entry](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/blob/develop/docs/reference/lab-gotchas.md#fences-and-failovers-when-the-host-is-cpu-starved).

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

To fire a bounded burst by hand, run `mqlab qm e2e`. Every request must round-trip
or the command exits non-zero:

```bash
# mqlab qm e2e <stack> [--count N]   (N defaults to 5)
mqlab qm e2e nativeha-ubuntu --count 20
```

It runs `mq-app-requester --count N` on `app-client` with the same settings the
always-on requester is deployed with, so nothing is typed by hand:

- **QM** is the stack's app queue manager, `<SHORT>APP` (for example `NHAUAPP`).
- **CONN** is the `app_conn` that the stack's provision playbook passes to
  `site-distributed-shared.yml`. That is the site-A instance list on the Native HA
  stacks and the VIP FQDNs on the Pacemaker and RDQM stacks.
- **TLS** uses the `app-requester` role's keystore stem (`/home/vagrant/ssl/key`) and
  certificate label.

It reaches the lab through the shared Vagrant state in `build/state/vagrant`, so it
works from any checkout or worktree. Like the other stack-scoped verbs, it refuses
a running stack that has no instance record.

## Failover drills

The always-on requester is the probe: induce a failure on the active node and
watch the stream dip and recover while the queue manager fails over. The
controlled, stack-dispatched lever is `mqlab qm`:

```bash
mqlab qm status <stack>    # HA resource / instance state
mqlab qm down <stack>      # stop the active QM, HA intact — the controlled-failover trigger
mqlab qm up <stack>        # bring it back
mqlab qm reload <stack>    # restart the active QM, telling clients to reconnect (endmqm -r)
```

What each verb runs is per-mechanism (all from the stack's topology `verbs:`):
Pacemaker disables/enables the `mq_group` resource (`pcs`), RDQM runs
`endmqm -w` / `strmqm`, and Native HA stops/starts the `mqmonitor@` unit so the
raft group elects a new leader.

On the two Native HA stacks, `qm status` runs `dspmq -o nativeha -x` on the first
node of each site: site A shows the Live group and site B the Recovery group, each
with its own quorum and instance sync. If site B is not running (`bootstrap
--no-dr`), its query is skipped with a note instead of failing. RDQM and Pacemaker
`qm status` runs on site A only; for RDQM, `rdqmstatus` there already shows the DR
relationship.

`qm reload` is a planned restart with client reconnect: IBM's `endmqm -r`
semantics, so reconnectable clients start reconnecting instead of being dropped.
It pairs with the always-on requester to show clients riding through a planned
restart. It runs the `site-qm-reload.yml` playbook, which finds the one node whose
`dspmq -n` reads `STATUS(RUNNING)` (site A, or site B after a DR cutover; a DR
site that is not running is skipped with a note). It then uses IBM's documented
path for the arm:

| Stack | What runs on the active node | IBM source |
|---|---|---|
| Native HA | `systemctl reload mqmonitor@<QM>` (MQ 10.0.1+), then waits until `dspmq -o nativeha -x` shows full quorum, one Active, and every instance in sync | [Monitoring, restarting, and ending queue manager instances](https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/monitoring_nativeHA.html): "the equivalent of using the endmqm -r command" |
| RDQM | `endmqm -r -tp 29 -w <QM>`, then `strmqm <QM>`, on the RDQM's primary node; waits until `dspmq` shows `STATUS(RUNNING)` | [Starting, stopping, and displaying the state of a DR/HA RDQM](https://www.ibm.com/docs/en/SSYHRD_10.0.0/configure/q133988_.html): "All other endmqm parameters can be used when stopping an RDQM" |
| Pacemaker | refused, with the reason | — |

The `-r` meaning comes from the
[endmqm reference](https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=reference-endmqm-end-queue-manager).
The RDQM flags `-r -tp 29 -w` are the same ones IBM's own 10.0.1 `mqmonitor.py`
reload uses. On Native HA, ending the active instance makes the group elect a new
Active (endmqm reference), so the restarted instance usually comes back as a
Replica. The playbook reports which instance is Active afterwards.
`mqmonitor.py` reports a failed `endmqm` in its log but still exits 0, so the
playbook also reads the `mqmonitor` journal and fails unless it says the
instance stopped.

Pacemaker is refused because it manages the queue manager through a systemd
resource whose stop is `endmqm -w`, with no `-r`. A Pacemaker restart therefore
never tells clients to reconnect. An `endmqm -r` run behind Pacemaker's back is
a resource failure that Pacemaker recovers, not a planned restart.

Each step (the restart, then the wait for the queue manager) is bounded by
`--timeout` seconds (default 300). The run fails non-zero if a step goes past it,
and the playbook reports how long the queue manager took to come back.

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

`mqlab dr cutover|failback <stack> [--rpo0-drill]` runs the stack's **own**
declared DR verb — the `dr-cutover` / `dr-failback` entry under the stack's
`verbs:` in `lab/topology.yaml`. A verb names exactly one implementation:

- `script: <name>` — a `lab/scripts/` entry, run as
  `bash <script> <a2b|b2a> <QM>` (with `RPO0_DRILL=1` in its environment when
  drilling). `rdqm-rhel` declares `rdqm-dr-cutover.sh` this way.
- `playbook: <name>` — an `ansible/` playbook, run as
  `ansible-playbook <playbook> -e dr_direction=<a2b|b2a> -e qm_name=<QM> -e rpo0_drill=<true|false>`.

A stack that declares no DR verb is refused with a clean error (exit 2).

`--rpo0-drill` proves **RPO-0** (no message loss): it seeds a persistent message
before the cut and asserts it is present at the peer afterward — the survival
assertion behind the DR framing. It is accepted **only** for a stack whose DR
verb declares `rpo0: true` (it implements the seed and the verify). On any other
stack `mqlab dr` refuses with exit 2 and runs nothing — it never cuts over
without the check you asked for. Today that is `rdqm-rhel` and, once its DR
playbook lands, `pcmk-drbd-ubuntu`.

!!! note "Per-arm DR coverage as-built"
    The **Pacemaker/SAN** arm (`pcmk-ubuntu`) declares no DR verb, so
    `mqlab dr` refuses it; it cuts over through `lab/scripts/pcmk-dr-cutover.sh`.
    The **Native HA** arms (`nativeha-rhel-crr`, `nativeha-ubuntu`) declare their
    CRR switchover playbook (`ansible/site-nativeha-switchover.yml` /
    `…-ubuntu-switchover.yml`) as their DR verbs, so `mqlab dr cutover <stack>`
    makes site B the Live group and `mqlab dr failback <stack>` makes site A Live
    again. The playbook maps `dr_direction` to the site to make Live (`a2b` → B,
    `b2a` → A) and takes the queue manager from `qm_name`. Its first play checks
    the inputs on the controller and fails before any node is touched if the
    direction is missing or unknown. These verbs do not declare `rpo0: true`, so
    `--rpo0-drill` is refused on them (exit 2).
    The playbook still accepts the hand-run form, `-e target_live=b` to cut over
    and `-e target_live=a` to fail back, and refuses a run that passes both
    `target_live` and a `dr_direction` that disagrees with it. Run
    `mqlab parity` to print the cross-arm capability matrix.

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

## Using the MQ Console

mqweb (the MQ Console and the admin REST API) runs on every queue manager node. To
open a stack's Console from your workstation, run on the dev VM:

```bash
mqlab console open <stack>            # e.g. nativeha-rhel-crr; site A
mqlab console open <stack> --site b   # the DR site's endpoints
```

The verb finds the stack's live mqweb and forwards the dev VM's
`127.0.0.1:19443` to it. It then prints the URL, the logins, and how to reach the
forward from your workstation. Access takes two hops:

1. **Workstation → dev VM.** The `vergil.toml` record `9443|127.0.0.1:19443`
   makes vergil-vm install a relay on the VM's port `9443`, declared and reached
   exactly like the Grafana relay on `3000`. (The `anonymous` profile uses
   `9444`, beside Grafana's `3001`.) The verb prints both URLs:
    - **Local Lima VM.** Lima forwards the port to your Mac. Browse
      `https://localhost:9443/ibmmq/console/login.html`.
    - **Cloud VM.** Browse the VM's external IP, shown by `vrg-vm list`, on the
      same port: `https://<external-ip>:9443/ibmmq/console/login.html`. The
      cloud VM's firewall currently admits only `tcp:3000`, so this port answers
      only once vergil-vm adds a firewall rule for it. See
      [Reaching lab services from your workstation](#reaching-lab-services-from-your-workstation).
2. **Dev VM → live instance.** A socat forwarder (`mqlab-console-forward`)
   carries `127.0.0.1:19443` to the live instance's `:9443`. TLS passes straight
   through. For a VIP stack the live instance is the VIP. For Native HA, which has
   no VIP, `console open` asks each instance the list query
   `GET /ibmmq/rest/v3/admin/qmgr?attributes=*` as `mqreadonly` and picks the one
   reporting `running`. If none is running, as on the CRR recovery site before a
   switchover, it fails and names every instance's answer.

Log in with one of the three lab users. In this lab only, each password equals
its username:

| Login | IBM role | Can do |
|---|---|---|
| `mqadmin` / `mqadmin` | `MQWebAdmin` | Full administration |
| `mqreadonly` / `mqreadonly` | `MQWebAdminRO` | Read-only administration |
| `mqoperator` / `mqoperator` | `MQWebUser` | What the `mqoperator` MQ authority allows |

Two behaviours are expected:

- **The browser warns once about the certificate.** Accept and continue. The
  mqweb certificate names `mqweb`, not `localhost`, and it chains to the lab's
  private CA. The workstation deliberately does not trust that CA: the lab builds
  no trust link between the ephemeral lab and the host, and removing a one-time
  click-through is not worth that complexity.
- **Re-run `mqlab console open` after a failover or a DR cutover.** The forward
  points at one instance and does not follow the queue manager. A re-run finds the
  new live instance and replaces the forwarder. On a Native HA stack, a stale
  forward shows itself in the Console: the login works, but **Local queue
  managers** lists the queue manager with status `Replica` and it cannot be
  opened. That is the replica's own mqweb answering, not a fault.

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
mqlab obs relay-heal [--step]         # restart the Grafana port-forward relay + verify the forward
```

The workstation reaches Grafana through a `vergil-portforward-<vm-port>.service`
relay that vergil-vm installs from the VM profile's `port_forwards` in
`vergil.toml`. The port is per profile: `vergil-user` forwards `3000`, `anonymous`
forwards `3001`. `mqlab obs open` and `mqlab obs relay-heal` therefore discover the
relay from the installed units (`systemctl list-units --all
'vergil-portforward-*.service'`) rather than assuming `:3000`, and fail loud with
the remedy if no relay to Grafana (`10.50.0.2:3000`) is installed. How the
workstation reaches that port on a local or a cloud dev VM is described in
[Reaching lab services from your workstation](#reaching-lab-services-from-your-workstation).

The render verbs are pure topology→file projections; the observe phase of
`mqlab commons up` runs them and hands the output to `site-obs.yml`, so a normal
bring-up leaves the boards current without a manual render.

### Reaching lab services from your workstation

This section is the lab's single description of how a browser on your workstation
reaches a lab service. Other pages link here.

Lab services run on nested guests, such as Grafana on the `obs` guest at
`10.50.0.2:3000`. The workstation cannot address those guests directly. A
`port_forwards` record `"<vm-port>|<host:port>"` in the VM profile in
`vergil.toml` makes vergil-vm install a relay unit,
`vergil-portforward-<vm-port>.service`, on the dev VM. The relay is a `socat` that
listens on the dev VM's `0.0.0.0:<vm-port>` and forwards to `<host:port>` inside the
lab. The relay is the same on both backends. Only the last hop to the workstation
differs:

| Dev VM | Browse | Where the address comes from |
|---|---|---|
| Local Lima VM | `localhost:<vm-port>` | Lima forwards the VM's `0.0.0.0` listeners to the Mac |
| Cloud (GCP) VM | `<external-ip>:<vm-port>` | the **EXTERNAL IP** column of `vrg-vm list` |

The cloud dev VM has a public IP. You do not need a `gcloud` IAP tunnel, an
`ssh -L` forward or a relay of your own to reach a forwarded port. Open
`http://<external-ip>:3000/d/lab-watcher` for Grafana, the same way you would open
`localhost:3000` on a Lima VM. Three limits apply on the cloud VM:

- **The cloud firewall must admit the port.** vergil-vm's GCP module creates one
  ingress rule for browsing, `<vm>-browse`. It allows `tcp:3000` from any source
  and no other port. A `port_forwards` port other than `3000` works on a Lima VM
  but is blocked on the cloud VM until that rule admits it. Opening more ports is
  vergil-vm work, tracked here as
  [#345](https://github.com/logical-minds-foundry/mq-resiliency-lab-for-linux/issues/345).
- **The IP is ephemeral.** GCP can assign a new external IP when the VM is
  recreated or restarted. Read it from `vrg-vm list` each time. Never write it into
  the repo.
- **SSH is separate.** The VM's port `22` admits only Google's IAP address range,
  so `vrg-vm session` and file transfers such as `scripts/push-rhel-iso.sh` go over
  IAP-tunnelled SSH. That is a rule for SSH only. It does not mean the VM has no
  public IP, and it does not affect browsing a forwarded port.

Sources: vergil-vm `templates/provision/80-port-forwards.sh` (the relay unit),
vergil-vm `opentofu/modules/gcp/vm/main.tf` (the `browse` and `ssh` firewall rules
and the external IP), and the vergil-vm v2.1.36 release (vergil-vm #261), which
added the external IP.

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
