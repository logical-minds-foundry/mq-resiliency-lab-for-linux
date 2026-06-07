# Phase D — Ubuntu Pacemaker/SAN Arm Findings

> **Status:** HA (site A) complete with the fault suite; DR (DRBD-async)
> pending. Environment: three Ubuntu 24.04 arm64 KVM guests + a LIO iSCSI
> target VM on a dedicated severable SAN network. All timings are real
> (no emulation tax — this arm runs hardware-accelerated).

## Build facts

- 3-node corosync/pacemaker (heartbeat-net rings), `pcs` tooling,
  hacluster credential runtime-injected.
- **Real STONITH:** `fence_virsh` per node against the lab hypervisor
  over a dedicated ssh key; proven live (`pcs stonith fence pcmk-a3`
  power-cycled the actual domain). Hypervisor-side ssh-exec needs
  `LIBVIRT_DEFAULT_URI=qemu:///system` (zshenv).
- Shared storage: LIO target exporting one LUN (ACL-ed per initiator,
  portal on `net-san-a`), open-iscsi initiators with stable IQNs, XFS
  labeled `MQSHARED`, formatted exactly once.
- QM with `-md/-ld` on the LUN; definition distributed via
  `dspmqinf`/`addmqinf`; systemd unit deployed **disabled** on all nodes
  (Pacemaker is the only starter); resource group `mq_fs → mq_vip →
  mq_qm` with group ordering.
- Package facts: OCF agents are separate packages
  (`resource-agents-base`/`-extra`); `fence-agents-virsh` likewise.
- Tooling fact: this pcs version's `resource move` porcelain
  (create-wait-verify-rollback) raced our migrations and reverted them;
  **planned moves = explicit location constraint → verify → remove**,
  with `resource-stickiness=100` set so groups do not wander when
  constraints are removed (default stickiness 0 moved the group behind
  our backs once — found the hard way).

## §3.1 fault-suite results (identical drills to the RDQM arm)

Same discipline: persistent messages through the VIP pre/post fault,
ground-truth assertions, real timings (arm64/KVM).

| # | Drill | Observed |
|---|---|---|
| 1 | `kill -9` QM on owner | Monitor detected at **+23 s**, in-place restart, QM running **+34 s**, `failcount=1` recorded; 3/3 messages intact |
| 2 | Hard power-off owner | **Cluster-initiated fence** of the dead node (`client=pacemaker-controld` in stonith history), group migrated ~15 s, VIP followed, 2/2 messages intact; fence's reboot restored the node |
| 3 | Heartbeat partition on owner | Quorum side **fenced the isolated owner at ~+56 s** (fence-before-takeover — the double-mount guard), exactly one owner throughout, group recovered, partition healed by the fence's reboot; 2/2 messages intact, zero XFS corruption markers |
| 4a | **SAN severed under idle owner — defaults** | **~12 minutes of green status with dead storage.** Default `Filesystem` monitor checks the mount table only; an idle QM generates no I/O, so nothing touches the dead disk. Eventually detected; group recovered on a healthy node; `mq_qm` failcount escalated to INFINITY (node banned, not fenced — the stop path succeeded) |
| 4b | **SAN severed — hardened monitor** | With `op monitor interval=30s OCF_CHECK_LEVEL=20 on-fail=fence`: severance → **fence of the owner at +53 s** → group recovered on a storage-healthy node. One config line: 12 min silent → 53 s decisive |
| 5 | Planned move + return | Explicit-constraint move in **13–19 s** with message continuity via the VIP |

**Open anomaly (flagged, not buried):** during 4a's undetected window, a
persistent put through the VIP returned success (`AMQSPUT0 end`) but the
message was not on the queue after recovery. Timeline suggests it raced
the failover transition. RDQM's drills produced no such anomaly. Needs a
dedicated reproduction before Phase E treats it as evidence — but as a
question mark it already illustrates the §2.5 Q4 class: shared-storage
failure windows create message-integrity questions the shared-nothing
design never raises.

## The Q1/Q4 answers taking shape (spec §2.5)

- **Q1 (parity):** functional parity with RDQM's HA behaviors is
  *achievable* — automatic restart, failover, fencing, VIP tracking all
  demonstrated. The cost is visible above: every behavior is a component
  we configured, packaged, debugged, and now own (agents, fencing paths,
  monitor depths, stickiness, move semantics).
- **Q4 (storage SPOF):** demonstrated literally — `san-a` is one VM, and
  drill 4a shows the failure mode is *silent by default*. The
  defaults-vs-hardened contrast is the sharpest single exhibit in the
  arm-vs-arm ledger: RDQM has no equivalent failure class to tune away.

## Turnkey-vs-hand-built ledger (for Phase E)

| Behavior | RDQM | This arm |
|---|---|---|
| Cluster formation | `rdqmadm -c` | pcs auth/setup + packages + hosts + credentials |
| Fencing | implicit (DRBD quorum semantics) | fence_virsh + ssh keys + URI env + per-node primitives + constraints |
| Storage | per-node local, sync replicated | LIO target + initiators + XFS + the SPOF + monitor-depth tuning |
| QM as HA resource | `crtmqm -sx` | crtmqm on LUN + addmqinf + systemd unit + 3-resource group |
| Floating IP | `rdqmint -f` | IPaddr2 resource in the group |
| Planned move | `rdqmadm -s/-r` (preferred-location honored) | constraint dance + stickiness tuning (porcelain unreliable) |
| Storage-failure detection | N/A by design | OCF_CHECK_LEVEL=20 + on-fail=fence, or 12-minute blindness |

## Unplanned reproducibility test (crash recovery)

The dev laptop crashed between the HA suite and the DR build, destroying
**every** libvirt domain, all nine lab networks, the storage pool, and
the Vagrant box — the entire running lab, gone. Recovery was: recreate
the pool, `net-up.sh`, `vagrant up` (box re-pulled). **Site A's four
nodes were back from committed code in 86 seconds**; the playbooks then
rebuild the cluster identically. Nothing was lost but running state —
exactly the spec §0 "disposable per-run harness, durable model" claim,
proven by accident. (This is also the strongest possible argument for
the educational framing: a learner can wipe the whole thing and get it
back from git.)

## DR (DRBD-async) results

Because the crash forced a fresh build, site A was rebuilt **DR-ready
from the start** — DRBD under the LUN before the QM existed (the Phase C
lesson applied: don't bolt DR on a live system). Architecture: host-based
DRBD (protocol A, async) between `san-a` and `san-b` over `net-wan`; LIO
exports `/dev/drbd0` (not the raw disk) so the block storage itself
replicates cross-site; each site has its own 3-node Pacemaker cluster.

**Controlled cutover A→B (RPO 0):** 3 persistent messages put at site A,
DRBD driven to `UpToDate/UpToDate`, then the cutover runbook ran:
quiesce A → demote DRBD A → promote DRBD B → re-export the LUN at B →
B initiators log in → resource group created+started on cluster B in 13 s.
**All 3 messages retrieved at site B via B's VIP — RPO 0.**

**The step-count ledger (the sharpest single comparison):**

| | RDQM (Phase C) | This arm |
|---|---|---|
| Cross-site cutover | `rdqmdr -m QM -s` (old primary) + `rdqmdr -m QM -p` (new primary) — **2 commands** | quiesce cluster → confirm DRBD caught up → `drbdadm secondary` + `targetctl clear` at A → `drbdadm primary` at B → rebuild the LIO target on B → initiator logins on 3 nodes → create/start the resource group — **~7 coordinated steps across 3 host classes**, scripted as `pcmk-dr-cutover.sh` |
| Replication | continuous, built-in, automatic | DRBD configured, sync-rate-tuned by hand (dynamic controller throttled the initial 8 GB sync; `drbdsetup --c-plan-ahead=0` + a high static rate was needed) |
| What travels on cutover | QM + messages, automatically | QM + messages (via block replication) — but the QM *definition* must be pre-seeded on the peer (`addmqinf` + systemd unit), and initiator IQNs/ACLs must pre-match |

**Role gaps the DR build surfaced (each now a finding, several still
TODO in the roles):** the iSCSI-target package and the stable-IQN
initiator config must be present at *both* sites pre-cutover (the role
only configured the live site); `linux-modules-extra-<kver>` carries
DRBD on Ubuntu (no module ships by default — the mirror image of RDQM
shipping prebuilt kmods); the fence key authorization and
`LIBVIRT_DEFAULT_URI` are dev-VM state that the crash wiped and that a
real deployment would bake into the hypervisor.

## DR honesty note

Two of the cutover's failures during the live run were *operator-state*
gaps exposed by the crash (fence-key authorization, the libvirt URI),
not design flaws — but their existence *is* a finding: this arm has a
large surface of out-of-band state (ssh keys, IQNs, initiator configs,
DRBD tunables) that must be correct across both sites before DR works,
versus RDQM's self-contained `crtmqm -rr` / `rdqmdr`. The lab proved the
mechanism works; it also measured how much there is to get right.
