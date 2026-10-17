# mqweb per-node spike findings (#1499, epic logical-minds-foundry/.github#341)

> Scope: the gating spike (Task 1) of epic
> [logical-minds-foundry/.github#341](https://github.com/logical-minds-foundry/.github/issues/341)
> (MQ Web first-class). It answers the epic spec's §3 questions with per-node mqweb
> turned on, from a cold bootstrap of `nativeha-rhel-crr` on IBM MQ `10.0.1.0`:
>
> 1. What does mqweb cost per node (cold start and memory)? Does the one-pass cold
>    rebuild still hold?
> 2. What do REST and the Console show on a Native HA replica and on a CRR recovery-group
>    instance?
> 3. Which REST API version should the lab use?
>
> **Status: both halves done.** The gate was decided on cloud x86 (this spike). By the
> human's decision (2026-10-16, `.github#358`) the **macOS/arm64** half was deferred to the
> epic's Task 6 validation. It ran as #1595 (SUCCESS, 2026-10-17), and #1614 filled its
> figures in here. Only the Ubuntu stacks run on arm64 (`.github#360`): IBM ships no
> RHEL-on-ARM MQ, so `nativeha-ubuntu` (QM `NHAUAPP`) stands in for `nativeha-rhel-crr`.
>
> Convention: **[data]** is something measured on the lab or quoted from a source.
> **[judgment]** is our reasoning on top of it.

## 1. Answers at a glance

| Spec §3 question | Cloud x86 answer | macOS/arm64 answer |
|---|---|---|
| 1. Per-node cost; one-pass rebuild? | Cold start about 21 s per node at bootstrap; warm restart 6–8 s; Java RSS about 165–175 MB steady. The bootstrap completed in **one pass** (29 min 22 s). | Cold start about 9–10 s per node at bootstrap; restart→ready 3.3–6.7 s; Java RSS about 180–240 MB steady. `pcmk-ubuntu` and `nativeha-ubuntu` each completed in **one pass** (35 min 36 s and 19 min 56 s). §3.3. |
| 2. Replica and CRR behaviour | mqweb runs and answers on every instance. The **named** query `GET /admin/qmgr/NHARCAPP` fails on every non-active instance with HTTP 500 (MQRC 2543). The **collection** query `GET /admin/qmgr` reports `replica` or `recoveryGroupLeader` there. The Console is served and accepts a login on every instance, but only the active instance can open the queue manager (§4.5). | **Identical** on `NHAUAPP`: same states, the same HTTP 500 / 2543 on the named query off the active instance, and the same Console behaviour on a replica. §4.6. |
| 3. REST version | `v1`, `v2` and `v3` all answer identically on 10.0.1.0; `v4` is 404. **`REST_API_VERSION=v3`**. | `v3` answers on every node; the other versions were not re-checked. |

**Verdict: PASS on cloud x86, after a PAUSE that the human resolved, and PASS on
macOS/arm64.** The named surprise was that a named-QM REST GET on a replica returns an
error, not `"state":"replica"`. The fix the human accepted is to probe with the collection
query; `.github#358` amends the spec and plan for it. arm64 behaves the same. See §6. The
cost and one-pass criteria pass on both platforms.

## 2. Method

### 2.1 Turning mqweb on

`mqlab bootstrap` has no `-e` pass-through. So, using the plan's fallback, the spike set
`mqweb_enabled: true` in `ansible/group_vars/all/admin.yml` as an **uncommitted scratch
edit** in the spike's worktree. The bootstrap then ran from that worktree:

```bash
uv run mqlab bootstrap nativeha-rhel-crr
```

The edit was reverted before committing. This document is the only change on the branch.

### 2.2 Host and node sizing (cloud x86)

| Item | Value |
|---|---|
| Host | Google Compute Engine VM, 16 vCPU (Intel Xeon @ 2.80 GHz), 62 GB RAM, KVM/libvirt |
| `MQLAB_ENV` | `cloud` (auto-detected) |
| QM nodes | `nha-rhel-crr-a1..a3`, `-b1..b3`: 2 vCPU, 2048 MiB (1704 MiB visible to the guest), `host-passthrough` CPU, no huge pages |
| Guest OS | RHEL 10.2 |
| MQ | `10.0.1.0`, `MQSeriesWeb-10.0.1-0.x86_64` |
| `mqweb.service` | `TimeoutStartSec=1800` (30 min), `Type=forking`, started `--no-block` by the role |
| mqweb JVM | Bundled IBM JRE; no `-Xms` or `-Xmx` in `jvm.options`, so the JVM's default heap sizing applies |
| mqweb properties | `httpHost=*`, `httpsPort=9443` |

### 2.3 Measurements

- **Bootstrap:** the run's own perf report (`perf-20261015T222146Z.json` in the
  state bucket's `runs/` directory) and the Ansible task timings for the role's
  readiness gate (`wait_for` port 9443, run per node with `serial: 1`).
- **Restart cold start:** plan Step 2, at millisecond resolution. On each node, restart
  `mqweb.service` and time until `curl https://127.0.0.1:9443/ibmmq/console/` gets any
  HTTP answer (`ready_s`). Then sum the Java RSS (`rss_kb`) at ready and again 60 s
  later. The nodes ran one at a time, three rounds each. Liberty's own
  `CWWKF0011I ... started in N seconds` figure is recorded alongside.

  > **Method correction (#1614).** As the plan writes it (Task 1 Step 2), the clock starts
  > *after* `systemctl restart mqweb.service` returns. `mqweb.service` is `Type=forking`,
  > so `restart` blocks through the stop and through `strmqweb` until Liberty is nearly
  > up. Timed that way, `ready_s` measures only the last fraction of a second: 0.07–0.32 s
  > on every macOS node (#1595). The arm64 half therefore also recorded
  > **`restart_to_ready_s`**, with the clock started *before* the restart. That is the
  > figure that means "how long a restart takes to serve again".
  >
  > [judgment] The cloud `ready_s` in §3.2 (6.3–8.4 s) cannot be the after-return
  > interval of the same unit; that reads well under a second. It most likely spans the
  > restart, so it compares with macOS `restart_to_ready_s`. The spike's exact command
  > was not recorded, so this is unconfirmed. See §7.
- **REST:** `curl` from the dev VM to each node's data-plane IP on `:9443`, as the
  current lab admin (`mqadmin`, password from the lab secret `mqweb_admin_password`).
  Both the named query (`/admin/qmgr/NHARCAPP`) and the collection query
  (`/admin/qmgr`) were tried on `v1`, `v2` and `v3`.
- **Console:** no browser was available, so the Console was checked over HTTPS with
  `curl` only, on the active instance, a replica, and the recovery-group leader.

## 3. Per-node cost (spec §3 Q1)

### 3.1 Bootstrap, cold rebuild (cloud x86) [data]

The bootstrap completed in **one pass**: exit 0, no re-run, `failed=0` and
`unreachable=0` in every play recap, and no retries in any phase.

| Phase | Seconds |
|---|---|
| `prereq:vms` (box bake: `mq-client` fat box) | 504.2 |
| `vms` (3 boot batches) | 451.9 |
| `prereq:provision` (PKI) | 56.0 |
| `provision` (of which the stack play took 384.6) | 506.7 |
| `observe` | 242.2 |
| **Total wall clock** | **1762 (29 min 22 s)** |

The provision-time mqweb readiness gate measured these cold starts. The gate is the
role's `wait_for` port 9443, with nodes one at a time:

| Node | Gate wait (s) | Result |
|---|---|---|
| `nha-rhel-crr-a1` | 21.7 | ready |
| `nha-rhel-crr-a2` | 20.7 | ready |
| `nha-rhel-crr-a3` | 20.7 | ready |
| `nha-rhel-crr-b1` | 20.7 | ready |
| `nha-rhel-crr-b2` | 20.7 | ready |
| `nha-rhel-crr-b3` | 20.7 | ready |
| `svc-sim` (SVCQM) | 20.5 | ready |

On `a2`, Liberty logged `The mqweb server started in 17.943 seconds` for that first
start. The other nodes' first-start logs had already rotated out; Liberty keeps two
`messages*.log` files. No node logged the role's "not ready within 300s" or "FAILED
to start" warnings. `systemctl is-active mqweb.service` returned `active` on all six
nodes after the bootstrap.

**Cloud x86, six nodes in total, with mqweb on: about 2 min of serialized readiness
gates.**

### 3.2 Restart cold start and memory (cloud x86) [data]

Three rounds per node, reported as min / median / max. What this `ready_s` measured is
unconfirmed; see the method correction in §2.3.

| Node | `ready_s` | Liberty start (s) | `rss_kb` at ready | `rss_kb` at +60 s |
|---|---|---|---|---|
| `nha-rhel-crr-a1` | 6.3 / 6.6 / 6.6 | 2.56 / 2.63 / 2.98 | 246884 / 249108 / 253816 | 168236 / 171836 / 172120 |
| `nha-rhel-crr-a2` | 6.7 / 6.8 / 8.4 | 2.61 / 2.82 / 3.28 | 246668 / 248612 / 252688 | 165940 / 170908 / 173400 |
| `nha-rhel-crr-a3` | 6.6 / 6.6 / 8.2 | 2.61 / 2.85 / 3.22 | 240660 / 244840 / 247892 | 164588 / 168484 / 178644 |
| `nha-rhel-crr-b1` | 6.5 / 6.7 / 7.9 | 2.71 / 2.80 / 3.20 | 240420 / 244284 / 247396 | 158912 / 165344 / 166888 |
| `nha-rhel-crr-b2` | 6.3 / 6.8 / 8.3 | 2.63 / 2.89 / 3.26 | 234636 / 240096 / 249856 | 161548 / 163164 / 171812 |
| `nha-rhel-crr-b3` | 6.7 / 6.8 / 8.2 | 2.78 / 2.96 / 3.18 | 240692 / 244820 / 245952 | 165076 / 169188 / 169416 |

During the runs the 1-minute load average on the nodes was 0.06–0.53.

After all measurements, with mqweb and the QM running, every node showed about
630–720 MiB used and about 985–1075 MiB available out of 1704 MiB. Before the restart
rounds, the steady RSS after bootstrap was 169628–178072 kB.

### 3.3 macOS/arm64 [data]

From #1595 (result comment of 2026-10-17). The host was the local Lima VM on Apple
silicon: aarch64, 24 vCPU, 64 GiB, nested KVM. The guests were Ubuntu, with QM nodes at
2 vCPU and 2 GiB. `MQLAB_ENV` was `macos` (auto-detected), and the only
`env_profiles.macos` lever in force was **`memory_backing: hugepages`**. mqweb ran on
every QM node by default (#1502), with no scratch edit.

| Stack | One pass | Total wall clock |
|---|---|---|
| `pcmk-ubuntu` (+ svc-sim and the commons) | **yes**: rc 0, no phase retries | 35 min 36 s |
| `nativeha-ubuntu` (after `pcmk-ubuntu` was torn down, for host memory) | **yes**: rc 0, no phase retries | 19 min 56 s |
| `nativeha-ubuntu`, a later cold rebuild for #1606 | **yes**: rc 0, no phase retries | 16 min 15 s |

An earlier `pcmk-ubuntu` attempt died in `prereq:vms` about 60 s in, when PyPI and ghcr
were unreachable from the host and before any VM existed. It does not count against
one-pass.

**Readiness-gate wait per node** (the role's `wait_for` 9443, nodes one at a time):

| Node | Gate wait (s) |
|---|---|
| `pcmk-a1..a3`, `pcmk-b1..b3`, svc-sim (`pcmk-ubuntu` run) | 9.24–9.30 each |
| `nha-ubuntu-a1` / `-a2` / `-a3` | 10.32 / 10.36 / 9.36 |
| `nha-ubuntu-b1` / `-b2` / `-b3` | 9.33 / 10.30 / 10.35 |
| svc-sim (`nativeha-ubuntu` run) | 9.32 |

No node logged "not ready". `systemctl is-active mqweb.service` returned `active` on
every QM node of both stacks and on svc-sim.

**Restart and memory**, three rounds per node, min / median / max. `restart_to_ready_s`
uses the corrected method (§2.3). `ready_s` as the plan writes it read 0.07–0.32 s
everywhere.

| Node | `restart_to_ready_s` | `rss_kb` at ready (MB) | `rss_kb` at +60 s (MB) |
|---|---|---|---|
| `pcmk-a1` | 3.62 / 3.65 / 3.89 | 245–258 | 221–225 |
| `pcmk-a2` | 4.05 / 4.59 / 6.36 | 247–251 | 217–229 |
| `pcmk-a3` | 3.56 / 4.02 / 4.16 | 262–273 | 238–240 |
| `pcmk-b1` | 3.54 / 3.96 / 4.02 | 258–264 | 228–237 |
| `pcmk-b2` | 3.63 / 3.70 / 3.99 | 255–258 | 220–224 |
| `pcmk-b3` | 3.54 / 3.80 / 6.13 | 245–256 | 217–230 |
| svc-sim (`pcmk-ubuntu` run) | 3.80 / 4.17 / 4.43 | 252–261 | 222–226 |
| `nha-ubuntu-a1` | 3.60 / 4.11 / 5.20 | 248–260 | 179–193 |
| `nha-ubuntu-a2` (active) | 4.06 / 4.06 / 6.68 | 260–273 | 224–231 |
| `nha-ubuntu-a3` | 3.85 / 4.00 / 4.03 | 255–259 | 193–225 |
| `nha-ubuntu-b1` | 3.81 / 4.09 / 4.52 | 257–270 | 187–193 |
| `nha-ubuntu-b2` | 3.62 / 4.26 / 4.79 | 260–275 | 191–194 |
| `nha-ubuntu-b3` | 3.97 / 3.98 / 4.63 | 262–265 | 186–194 |
| svc-sim (`nativeha-ubuntu` run) | 3.28 / 3.93 / 5.04 | 244–258 | 215–223 |

**Host CPU starvation (an observed macOS difference, not mqweb) [data].** While these
stacks were up, the Mac's 18 cores were oversubscribed: two running VMs had 36 vCPUs
between them, and the Mac sat at 100% CPU. The nested guests then froze for 3–163 s at
a time. Guest kernels logged soft lockups and RCU stalls, often on idle CPUs. Corosync
lost its token, so Pacemaker fenced `pcmk` nodes (four fences), and Native HA failed
over twice on `nativeha-ubuntu`. Both arms recovered by themselves. An A/B test with
mqweb stopped on every QM node still stalled (21 stalls in 20 min). Root cause and
evidence: #1602. #1602 shipped `mqlab vm starvation`, which names the condition, and a
lab-gotchas entry; the human accepted it as a constraint of a single-workstation lab.
Cloud x86 showed none of this.

### 3.4 Reading the numbers [judgment]

- On cloud x86, per-node mqweb is cheap. Cold start is about 21 s, which is 1.2% of the
  1800 s `TimeoutStartSec`, and a warm restart is under 9 s. About 170 MB of RSS
  leaves about 1 GB available on a 2 GiB node. Because of the role's serial gate, a full
  six-node arm adds about 2 min to provision. Nothing here argues against D3, the
  register's "six or more Liberty JVMs on 2-vCPU nodes".
- The restart figures are **warm**: the OS page cache and Liberty's `workarea` are
  already populated. The figure to compare across platforms is the bootstrap gate
  (§3.1), not §3.2. The historic macOS failure (VAL-A, #1154) happened at cold start
  under TCG emulation of these x86-pinned RHEL nodes. That combination no longer occurs:
  arm64 runs only the Ubuntu stacks, natively (`.github#360`).
- On macOS/arm64 (§3.3), per-node mqweb is cheap too. The bootstrap gate is 9–10 s,
  about half of cloud's 21 s, and restart→ready is 3.3–6.7 s. The platforms ran
  different stacks (Ubuntu on arm64, RHEL on cloud), so this is a like-for-like check of
  the criteria, not a benchmark between them. Steady RSS is higher than cloud's: about
  180–240 MB against 160–180 MB. That still leaves well over 1 GB on a 2 GiB node.
  Nothing here argues against D3 on macOS either.
- The macOS stalls and fences (§3.3) come from the host, not from mqweb. They void any
  timing taken while they happen. The figures above came from windows without starvation
  as far as checked, but a single-workstation lab can drift into starvation at any time.
  Check with `mqlab vm starvation` before trusting a macOS measurement.
- RSS falls by about 75 MB within 60 s of ready, from start-up allocation to steady
  state. For sizing, use the steady figure plus headroom for Console sessions. This
  spike did not measure RSS under Console load.

## 4. Replica and CRR behaviour (spec §3 Q2)

### 4.1 Topology at measurement time [data]

`dspmq -m NHARCAPP -o nativeha -x` reported:

| Node | Data IP | Native HA role | `dspmq` STATUS | Group |
|---|---|---|---|---|
| `nha-rhel-crr-a1` | `10.10.1.91` | Replica | Replica | Live |
| `nha-rhel-crr-a2` | `10.10.1.92` | **Active** | Running | Live |
| `nha-rhel-crr-a3` | `10.10.1.93` | Replica | Replica | Live |
| `nha-rhel-crr-b1` | `10.10.2.91` | Leader | Recovery group leader | Recovery |
| `nha-rhel-crr-b2` | `10.10.2.92` | Replica | Replica | Recovery |
| `nha-rhel-crr-b3` | `10.10.2.93` | Replica | Replica | Recovery |

### 4.2 REST per node and API version [data]

Each row was identical across `v1`, `v2` and `v3`:

| Node | `GET …/admin/qmgr/NHARCAPP` (named) | `GET …/admin/qmgr` (collection) |
|---|---|---|
| `a1` | HTTP 500, `MQWB0004E`, reason 2543 `MQRC_STANDBY_Q_MGR` | 200, `state: replica` |
| `a2` (active) | 200, `state: running` | 200, `state: running` |
| `a3` | HTTP 500, `MQWB0004E`, reason 2543 | 200, `state: replica` |
| `b1` (leader) | HTTP 500, `MQWB0004E`, reason 2543 | 200, `state: recoveryGroupLeader` |
| `b2` | HTTP 500, `MQWB0004E`, reason 2543 | 200, `state: replica` |
| `b3` | HTTP 500, `MQWB0004E`, reason 2543 | 200, `state: replica` |

Other observations [data]:

- `GET …/v3/admin/qmgr?attributes=*` on a replica and on the recovery-group leader
  returns HTTP 200 with the full `extended` block. That block includes `role`
  (`replica` or `leader`), `groupName` (`Live` or `Recovery`), `groupRole`
  (`live` or `recovery`), `instanceName`, `inSync` and `quorum`. The collection query
  rejects `status=*` with HTTP 400 `MQWB0019E`.
- On the active instance, the named `…/v3/admin/qmgr/NHARCAPP?attributes=*&status=*`
  returns `role: active`, `groupRole: live`, and a `status` block (`connectionCount`,
  `channelInitiatorState`, `started`).
- `GET …/admin/installation` reports `version: 10.0.1.0` on every node.
- `GET …/v4/admin/qmgr` returns 404.
- An unauthenticated REST GET returns 401.
- Every node serves TLS 1.3 (`TLS_AES_256_GCM_SHA384`) with the org-CA certificate
  (`CN=mqweb`, issuer `app-org Root CA`).

### 4.3 What this means [judgment]

- **The surprise.** The named GET needs a connection to a running queue manager, and a
  replica refuses it with `MQRC_STANDBY_Q_MGR`. Only the collection GET, which reads
  the local `dspmq`-equivalent view, reports `replica`. The runbook note in
  [`mqweb-endpoint-verification.md`](mqweb-endpoint-verification.md) ("REST returns the
  QM with `"state":"replica"`") is accurate **for the collection query it shows**. Spec
  §2.3 cites that note, but it does not say which path. The plan's Task 5 `qm_state`
  uses the **named** path and treats any `HTTPError` as a login failure (`ProbeError
  "… check the mqweb login"`). As written, `resolve_live` would fail loudly on the
  first replica candidate instead of skipping it.
- **The cheap fix** (for Task 5, not for this spike). Probe
  `GET /ibmmq/rest/v3/admin/qmgr?attributes=*` and pick the record whose `name` is the
  QM. The probe then sees `running`, `replica` or `recoveryGroupLeader` and never gets
  an error on a healthy replica. This also gives `role`, `groupRole` and `instanceName`
  for the login printout.
- **CRR.** The recovery-group leader reports `recoveryGroupLeader`, not `running` or
  `replica`. A probe that accepts only `running` handles it correctly. Tests should
  include it as a candidate state.

### 4.4 Console [data, partial]

| Request | Active (`a2`) | Replica (`a1`) | Recovery leader (`b1`) |
|---|---|---|---|
| `GET /ibmmq/console/`, no credentials | 401, JSON `MQWB2010E: User is not authenticated.` | 401, same | 401, same |
| `GET /ibmmq/console/login.html`, no credentials | 200, `text/html`, `<title>MQ Console</title>` | 200 | 200 |
| `GET /ibmmq/console/`, HTTP basic as `mqadmin` | 200, `text/html`, `<title>MQ Console</title>` | 200 | 200 |

- The Console application starts and is served on **every** instance, including
  replicas and the recovery-group leader. Liberty logged `CWWKZ0001I: Application
  com.ibm.mq.console started` and `CWWKT0016I` for `/ibmmq/console/`.
- **A secondary surprise.** The lab still renders `mqweb_console_anonymous: true`, which
  binds `MQWebAdmin` to `special-subject EVERYONE` for `com.ibm.mq.console`, and the
  rendered `mqwebuser.xml` on the node does contain it. Even so, an unauthenticated
  `GET /ibmmq/console/` returns 401 on 10.0.1.0, and only `login.html` is public. Over
  `curl`, the "Console loads with no login" behaviour (#255, #258) does not show on
  10.0.1.0. A browser behaves the same way (§4.5).
- What a browser sees after login: §4.5.

### 4.5 Console in a browser [data]

Checked twice on 2026-10-16:

- **By the human:** Safari on macOS, through an IAP SSH tunnel and a relay to the active
  instance.

  > **Correction (2026-10-16, #1592).** This records the route used for the check. It is
  > not the access model. The cloud dev VM has a public IP, and a `port_forwards` port is
  > browsed at `<external-ip>:<port>` with no tunnel; see
  > [Reaching lab services from your workstation](../site/docs/operate/index.md#reaching-lab-services-from-your-workstation).
  > A tunnel was needed for `:9443` here only because vergil-vm's cloud firewall admits
  > `tcp:3000` alone.
- **Headless:** Chromium (Playwright) on the dev VM, logged in as `mqadmin` on each of
  `a2`, `a1` and `b1`.

The screenshots are in the temp bucket under `$(mqlab build path temp)/mqweb-console-spike/`.

- **Anonymous access does not work, and it hides the login page.** With
  `mqweb_console_anonymous: true`, a browser opening `/ibmmq/console/` gets the raw JSON
  `{"message": "MQWB2010E: User is not authenticated."}`. It is not redirected to the
  login page. The only way in is to open `/ibmmq/console/login.html` directly. After
  that, login as `mqadmin` succeeds.
  - [judgment] On 10.0.1.0 the `EVERYONE` grant is worse than ignored: it seems to
    suppress the Console's normal unauthenticated redirect.
  - Task 2 retires it. Task 7 should confirm that a plain `/ibmmq/console/` visit then
    reaches the login page.

| View | Active (`a2`) | Replica (`a1`) | Recovery leader (`b1`) |
|---|---|---|---|
| Home, "Your queue managers" | 1, counted as Running | 1, counted as **Running** | 1, counted as **Running** |
| Manage → Local queue managers, status | `Running` | `Replica` | `Recovery group leader` |
| Open `NHARCAPP` | Overview loads: CPU, memory, storage, queues, channels, connections, message rates | **Row does not open**; the view stays on the list | **Row does not open**; the view stays on the list |
| Queue-manager tabs | Overview, Queues, Events, Applications, MQ network | none | none |

- **Only the active instance's Console can administer the queue manager.** On a
  replica or the recovery-group leader, the Console lists the queue manager with its
  Native HA role but cannot open it. This is the browser counterpart of the REST
  reason-2543 behaviour in §4.2.
- **The home page misleads on non-active instances.** It counts the queue manager as
  Running on a replica and on the recovery-group leader. Only the Manage list shows the
  real role.
  - [judgment] A human pointed at a replica's Console would see "1 Running" and then be
    unable to open it. This is the strongest argument for `console open` following the
    active instance rather than using a fixed node.
- **No Native HA or replication status view** on the active instance's queue-manager
  page in 10.0.1.0. The tabs are as listed in the table. `View configuration` was not
  explored. The Native HA role is visible only as the status column of the Local queue
  managers list.

### 4.6 macOS/arm64: REST and Console on `nativeha-ubuntu` [data]

From #1595. Logged in as `mqreadonly` (the role logins replaced the single admin
credential in Task 2), with the list query `GET …/v3/admin/qmgr?attributes=*` and the
per-QM query `GET …/v3/admin/qmgr/NHAUAPP`:

| Node | Collection query | Named query |
|---|---|---|
| `a2` (active) | 200, `running` (active / live) | 200, `running` |
| `a1`, `a3` | 200, `replica` (live) | HTTP 500, `MQWB0004E`, reasonCode 2543 |
| `b1` (leader) | 200, `recoveryGroupLeader` (leader / recovery) | HTTP 500, `MQWB0004E`, reasonCode 2543 |
| `b2`, `b3` | 200, `replica` (recovery) | HTTP 500, `MQWB0004E`, reasonCode 2543 |

This **matches cloud x86 (§4.2) exactly**. The raw 500 bodies on `a1`, `b1` and `b2` give
`MQRC_STANDBY_Q_MGR`.

Console, checked by the human in a browser on the Mac through `mqlab console open`:

- The bare `/ibmmq/console/` now **redirects to the login page**, where §4.5 got
  `MQWB2010E` JSON. This confirms Task 2's retirement of anonymous access.
- `mqadmin`, `mqreadonly` and `mqoperator` all log in, and each role behaves as
  expected. The certificate warning came up once only.
- The first attempt reached a **replica**. The forward had been opened against `a2`,
  and Native HA then failed over to `a1` (host starvation, §3.3). The Console listed
  `NHAUAPP` as `replica` and would not open it, exactly as in §4.5. Re-running
  `mqlab console open` moved the forward to the active instance. This is the behaviour
  `console open` documents ("re-run after a failover").

## 5. REST version (spec §3 Q3)

[data] On MQ `10.0.1.0`, `v1`, `v2` and `v3` gave identical answers on every node and
for both queries in §4.2. `v4` is 404.

[judgment] `v2` still works, but the 10.0.x documentation uses `v3`, and `v3` answers.
Per plan Step 4:

```text
REST_API_VERSION=v3
```

## 6. Verdict

Plan PASS criteria, against the cloud x86 data:

| Criterion | Cloud x86 | macOS/arm64 |
|---|---|---|
| Every node ready within `TimeoutStartSec` (1800 s) | **Met.** Cold start about 21 s; warm restart at most 8.4 s | **Met.** Cold start 9–10 s; restart→ready at most 6.68 s |
| Bootstrap one-pass | **Met.** Exit 0, no re-run, no phase retries | **Met.** `pcmk-ubuntu` and `nativeha-ubuntu`: exit 0, no phase retries |
| Replica states as expected | **Not met as specified.** The named-QM GET errors on replicas (§4.2). The states are as expected only on the collection query | **Same as cloud x86** (§4.6), so it is met under the accepted resolution (collection query) |

**Spike result (cloud x86): PAUSE: a named-QM REST GET
(`/admin/qmgr/<QM>`) on a non-active Native HA instance returns HTTP 500 `MQWB0004E`
(MQRC 2543 `MQRC_STANDBY_Q_MGR`), not `"state":"replica"`.**

**Final gate verdict: PASS on cloud x86.** On 2026-10-16 the human accepted the
resolution below, which `.github#358` writes into the spec and plan. The human also
deferred the macOS/arm64 half to T6/T7 and accepted the small risk that arm64 differs.
Tasks 2 and 3 may start.

[judgment] This is the kind of surprise spec §3 says pauses the epic: it breaks the plan's
Task 5 `qm_state` and `resolve_live` as written. It does not argue against per-node
mqweb itself. Every cost and reliability figure on cloud x86 is comfortably inside the
criteria. The resolution:

1. Amend spec §2.3 to name the **collection** query
   (`GET /ibmmq/rest/v3/admin/qmgr?attributes=*`) as the probe's source of `state`.
   Add `recoveryGroupLeader` to the states the probe handles.
2. Amend plan Task 5's `qm_state` to use that query and select by `name`. Add a test in
   which a healthy replica answers `replica`, and keep "HTTP 401 or 403 means fail
   loud".
3. Record the anonymous-Console observation (§4.4) as context for Task 2. No deviation
   is needed, because Task 2 removes anonymous access.

No new IBM deviation (spec §8) came out of the cloud half. The figures support D3
as written for cloud x86.

**macOS/arm64 verdict: PASS (#1595).** Every criterion is met, and the REST and Console
behaviour is identical to cloud x86. That closes D3's macOS risk. The one macOS
difference, host CPU starvation that fences or fails over the cluster arms (§3.3, #1602),
comes from the host. It is not an mqweb finding, and it raises no IBM deviation.

## 7. Remaining work

- [x] **macOS/arm64 half** (#1595; filled by #1614). It ran on `nativeha-ubuntu` and
  `pcmk-ubuntu`, not `nativeha-rhel-crr`, which cannot run on arm64 (`.github#360`).
  §3.3, §4.6 and the macOS column of §1 and §6 hold it. arm64 did not differ materially.
- [x] **Console in a browser** (§4.5, and §4.6 for arm64).
- [x] **Final verdict** (§6). The spec §3/§8 amendments are in `.github#358`.
- [ ] **Confirm what the cloud `ready_s` measured** (§2.3). Next time a cloud stack is up,
  re-time a few restarts with both clocks (`ready_s` and `restart_to_ready_s`), and
  relabel §3.2 if needed. No criterion depends on it: either reading is far inside
  1800 s.

The cloud lab used for the cloud figures was left running with mqweb on.
