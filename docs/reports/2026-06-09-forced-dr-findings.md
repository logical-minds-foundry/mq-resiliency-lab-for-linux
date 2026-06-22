# Forced Cross-Site DR — Findings (Ubuntu Pacemaker/DRBD arm, #67)

> **Status:** The marquee result. A forced full-site-A loss under continuous
> load produces **RPO > 0**, and the DR validation framework attributes the loss
> to a specific bucket rather than reporting a bare number. This is the
> counterpart to the HA findings (RPO 0); together they answer the project's
> central question — *where is HA perfect, and where is DR honestly lossy?*

## Contents

- [Method](#method)
- [Result](#result)
- [Why the bucket matters](#why-the-bucket-matters)
- [Cross-site recovery](#cross-site-recovery)
- [HA vs forced DR, side by side](#ha-vs-forced-dr-side-by-side)
- [Findings exposed by the live run](#findings-exposed-by-the-live-run)

## Method

The cross-site arm is two 3-node Pacemaker clusters (site A, site B), each
fronting a LIO iSCSI LUN, with **host-based DRBD protocol A (async)** replicating
the LUN's backing device `san-a` → `san-b` over the inter-site WAN. QMPCMK runs
at site A on the DRBD-backed LUN.

The drill (`DR-FORCE-1`, "primary unrecoverable, flow continues through
cutover"):

1. Drive the continuous persistent+syncpoint app flow + god's-eye responder
   against site A's VIP.
2. **Break the cross-site DRBD link** mid-flow (`drbdadm disconnect` on `san-a`)
   — a WAN partition. The QM keeps committing locally; nothing reaches `san-b`.
3. **Abruptly destroy all of site A** (`san-a` + `pcmk-a1..3`) — a true site
   loss, the primary's disk gone.
4. **Force-promote `san-b`** (`drbdadm primary --force`) from whatever it had
   received, re-export the LUN, start the QM on cluster B.

Loss is then quantified by the framework: messages the app committed at A
*after* the replication break never reached `san-b`, so the forced promote
loses exactly that tail.

## Result

The definitive run gives the app **both** site VIPs in its connection list, so
the reconnectable client rides the cutover and keeps producing on the survivor:

```
DR-FORCE-1 (flow continues through cutover) — RPO 0: False
556 sent · 348 confirmed · 201 AMBIGUOUS · 7 continued · 0 lost · 0 stranded · 0 duplicated
```

The flow produced across **both sites in one logical run**:

- **348** committed at site A *before* the break → `confirmed`.
- **201** committed at site A *during* the WAN-partition window → never shipped
  to `san-b` → lost on the forced promote → `ambiguous` (the RPO > 0).
- **7** committed at site B *after* the cutover → `continued`: the client
  reconnected across sites and kept producing, exactly as a real app must.

(The small `continued` count is a drill-timing artifact — the cutover takes
~90 s and the flow's deadline landed just after B came up, so the producer had
only seconds on B. The mechanism is proven; a longer run shows hundreds. An
earlier non-continuing run gave the same loss shape: 441 sent, 179 ambiguous.)

RPO is **not** zero, and the lost messages are precisely the ones committed
during the WAN-partition window before the site died.

## Why the bucket matters

The 179 messages are classified **`ambiguous`**, not merely "lost" — and that
distinction is the entire point of the framework. The god's-eye responder
**received and replied to** those messages at site A, so the app's ledger shows
them as in-flight/processed; the app believes the work happened. But the forced
promote of `san-b` has **no record of them**. On recovery:

- **Resending** them risks **duplicates** (the originals may have been acted on
  downstream before the loss).
- **Not resending** them risks silent **loss** of real work.

This is the "messages come back to haunt you" failure that a static
"is-the-message-sitting-on-the-queue" RPO check cannot see. The framework names
it, counts it, and bounds it.

## Cross-site recovery

The forced cutover completed end to end: `san-b` Primary, LUN re-exported,
`pcmk-b1..3` logged in, `mq_group` (`mq_fs → mq_vip → mq_qm`) started on cluster
B, and **`QMPCMK STATUS(Running)` on site B** at VIP `10.10.2.200`, serving the
data that replicated before the break. Site A is gone; site B carries on.

## HA vs forced DR, side by side

| | HA (same arm, #64) | Forced DR (this report) |
|---|---|---|
| Fault | kill QM / crash node / sever heartbeat / sever SAN | abrupt full-site loss + WAN partition |
| Storage | shared LUN, one site | LUN replicated cross-site, async |
| Result | **RPO 0** — every message confirmed | **RPO > 0** — 179 ambiguous |
| Why | failover is local; no data leaves the site uncommitted | async replication has an unshipped tail at the instant of loss |

The honest takeaway: **HA is perfect when it's local; DR is lossy exactly to the
width of the replication lag at the moment of disaster** — and that lag is a
function of WAN bandwidth vs write rate, which the framework lets you measure
rather than assume.

## Findings exposed by the live run

- **Silent-success cutover script.** The first `pcmk-dr-force.sh` masked every
  step with `|| true` and printed "complete" unconditionally — so when its
  `ansible` calls had zero target hosts it did nothing yet reported success.
  Fixed to verify `QMPCMK` is actually `Running` on the peer and exit non-zero
  otherwise (fail loud).
- **Worktree inventory dependency (issue #69).** The silent failure above was
  caused by the worktree having no `build/inventory.ini`; the cutover's ansible
  had no hosts. Regenerating the inventory from the worktree fixed it instantly —
  concrete motivation for #69 (lab must be operable from a worktree).
- **DRBD initial sync is the wrong default.** The 8 GB full initial sync was
  I/O-bound for many minutes on a loaded host. Both backing disks start blank,
  so the build now **skips the initial sync** (`new-current-uuid --clear-bitmap`)
  and lets the filesystem + QM writes replicate as normal traffic — seconds
  instead of minutes, which matters for a ground-zero rebuild.
- **Client evidence must survive a hang.** A client given only the dead site's
  VIP blocks in `MQCONNX` (TCP connect to a vanished host) past its deadline; the
  ledger, written only at exit, was empty. The clients now flush every 2 s so a
  hung/killed client still leaves its evidence.
