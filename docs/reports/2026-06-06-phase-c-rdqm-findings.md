# Phase C — RDQM Arm Findings

> **Status:** in progress — HA (site A) complete; DR (3+3) pending.
> Environment: three RHEL 9.6 x86-64 guests under TCG on the dev VM
> (functional validation only per spec §6; all timings qualitative).

## Build facts

- **No IBM entitlement needed (spec §11 answered):** the no-charge MQ
  Advanced for Developers `LinuxX64` tar ships `MQSeriesRDQM`, the LINBIT
  Pacemaker/corosync stack, `drbd-utils`, and prebuilt `kmod-drbd` for
  el9 kernels `5.14.0-284.x` → `611.x`.
- **Fully offline:** RHEL installed by kickstart from the DVD ISO; the
  same ISO mounts in-guest as the BaseOS/AppStream dnf repo (SATA cdrom —
  q35 has no IDE). No registration, no network repos, ever.
- **Kernel/kmod:** guest kernel `5.14.0-570.12.1.el9_6` = exact shipped
  kmod match; `modprobe drbd` clean.
- Install sequencing: all rpms before any QM exists; `MQSeriesRDQM` in
  its **own** rpm transaction after the server files are on disk (its
  preinst fails inside a combined transaction).
- QM creation: secondaries `crtmqm -sxs` **before** primary `-sx`; as
  root (AMQ7077E as mqm); VIP via `rdqmint -f <addr> -l <iface>`.

## §3.1 fault-suite results (HA group, site A)

All drills: **RPO 0** (persistent messages seeded pre-fault, retrieved
post-fault via the floating IP), **zero manual intervention** in the
recovery itself.

| # | Drill | Observed |
|---|---|---|
| 1 | `kill -9` all QM processes on the primary | Pacemaker restarted the QM **in place** within ~a minute; 3/3 messages intact |
| 2 | Hard power-off of the primary (`virsh destroy`) | Automatic promotion of a2, **floating IP followed**, 2/2 messages intact; on power restore the node rejoined, resynchronized, and the QM **automatically failed back to the preferred node** |
| 3 | Heartbeat/replication link severed on the primary | **No split-brain:** the isolated primary surrendered the QM (`Status not available` / role `Unknown`); the quorum side promoted exactly one Primary; VIP moved; 2/2 messages put pre-partition retrieved post-partition |
| 5 | Planned failover + failback (`rdqmadm -s` / `-r`) | Controlled move to a survivor; traffic served via the VIP mid-move; on resume the QM returned to the preferred node automatically; all-Normal end state |

**Timing note (qualitative, TCG):** in-place restart ≈ tens of seconds;
node-loss failover landed between polls — minutes-scale at worst under
emulation; planned moves comparable. None of these wall-clocks are
representative of real hardware (spec §6) — the *behaviors* are the result.

## The Q2 ledger (spec §2.5) — what RDQM gave turnkey

Everything in the table above came from `rdqmadm`/`crtmqm -sx` defaults:
quorum, fencing-equivalent behavior, monitor/restart, VIP management,
preferred-location failback, synchronous block replication. The Phase A/B
stack had to hand-build network severability tests, boot services, and
persistence discipline to get *near* this; Phase D (Ubuntu/Pacemaker/SAN)
must construct **all of it** explicitly. That asymmetry — at equal
delivered functionality — is the §10 headline comparison point.

Operational notes for the ledger's Day-2 column:

- `rdqmstatus` on the *isolated* node reports honestly degraded state
  (`Unknown`) rather than stale claims — good 3 a.m. ergonomics.
- The suspend/resume verbs map exactly to spec §8.4's planned-maintenance
  story (`evacuate-node`).
- vagrant-libvirt leaves custom extra-disk volumes behind on destroy —
  lab hygiene: sweep `vol-list` after destroying RDQM nodes.

## DR (3+3) — pending

Site B build, HA/DR-combined QM, `rdqmdr` cutover/failback: next.
