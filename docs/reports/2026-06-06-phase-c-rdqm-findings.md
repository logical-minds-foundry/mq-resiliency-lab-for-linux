# Phase C — RDQM Arm Findings

> **Status:** complete — HA (site A) and DR (3+3) both demonstrated.
> Environment: three RHEL 9.6 x86-64 guests under TCG on the dev VM
> (functional validation only per spec §6; all timings qualitative).

## Contents

- [Build facts](#build-facts)
- [§3.1 fault-suite results (HA group, site A)](#31-fault-suite-results-ha-group-site-a)
- [The Q2 ledger (spec §2.5) — what RDQM gave turnkey](#the-q2-ledger-spec-25--what-rdqm-gave-turnkey)
- [DR (3+3) results](#dr-33-results)

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

## DR (3+3) results

Site B formed by the same roles (fresh nodes, zero failures). The HA-only
QM was deleted and recreated as the documented HA/DR-combined shape:
`crtmqm -sx[s] -rr p` at site A / `-rr s` at site B, DR replication on
the net-wan addresses, port 7001, async (default). The tool prints the
exact remote-side command — used verbatim.

| Step | Observed |
|---|---|
| Replication | `DR status: Normal` immediately after both sides enabled |
| Controlled cutover A→B (`rdqmdr -s` at A, `-p` at B) | QM running at site B **69 s** after the command; site-B floating IP added; **all 3 pre-cutover persistent messages retrieved via B's VIP — RPO 0** |
| Business at B | message accepted through 10.10.2.100 |
| Failback B→A | QM back at site A in **104 s**; the B-era message retrieved at A — **RPO 0 both directions** |

This is spec 3.1 step 7 (controlled variant), the 8.5 paved path
(confirm-replication-then-cut), and the 4.7 reversible rotation primitive
in one sequence. Disaster-variant cutover (site A powered off mid-flight)
is Phase E fault-suite material.

Additional Day-2 facts for the ledger:

- `mqm` must be in `haclient` or `endmqm`/`strmqm` under HA control
  fail with AMQ7077E (now encoded in the install role).
- RDQM verbs (`crtmqm -sx`, `dltmqm`, `rdqmdr`, `rdqmint`) run as
  root; plain MQ verbs as mqm.
- An HA-only RDQM cannot be converted in place - delete and recreate
  with the `-rr` flags (queue manager contents are lost; do DR-combined
  creation from the start in any real deployment).
- The QM's objects and messages travel with DR replication - the
  recovery site needs no content re-apply, only its floating IP.
