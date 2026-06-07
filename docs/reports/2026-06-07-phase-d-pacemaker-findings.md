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

## DR (DRBD-async) — pending

`san-b` + site-B cluster, DRBD under LIO over `net-wan`, controlled
cutover runbook: next.
