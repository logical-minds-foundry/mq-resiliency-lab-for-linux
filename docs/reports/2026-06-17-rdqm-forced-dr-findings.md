# RDQM 3+3 cross-site HA/DR — forced-DR drill findings (Plan C, #233)

**Date:** 2026-06-17 · **Setup:** `rdqm_dr` (QMRDQM across rdqm-a1/2/3 + rdqm-b1/2/3,
RHEL 9.6 x86_64 under TCG). **Substrate:** RHEL 9.6 / IBM MQ 9.4.5 RDQM.
**Scope:** prove a DR/HA RDQM can be built from scratch across 3+3 and survive a
forced (disaster) cutover to the recovery site, with failback. **TCG = functional
only; no timing claims** (latency/RPO figures here are qualitative).

## Result (summary)

- ✅ **Built a DR/HA RDQM across 3+3** — `site-rdqm-dr.yml` (both HA groups DR-ready) +
  `rdqm-dr-qm-create.sh` (DR/HA create). Healthy: site A HA Primary / **DR primary**,
  FIP `10.10.1.100`; site B DR **secondary** (QM standby), FIP `10.10.2.100`; both HA
  groups `Normal`, DR status `Normal`.
- ✅ **Forced-DR cutover works** — a hard power-off of all three site-A nodes followed
  by `rdqmdr -m QMRDQM -p` on rdqm-b1 brought QMRDQM up on site B in ~10 s, started
  automatically by the HA subsystem.
- ✅ **Failback** — site A powered back on, the dual-primary was reconciled (`rdqmdr -s`
  on A), then `rdqm-dr-cutover.sh b2a` returned the active role to site A. End state:
  A = DR primary / QM `Running` (FIP `.1.100`), B = DR secondary standby, both DR
  `Normal`. **Full cycle (build → forced cutover → reconcile → failback) demonstrated.**
- ◻️ **RPO-with-data measurement** — deferred; see §RPO (a client-connectivity wrinkle
  to resolve first).

## What was done

1. **Substrate.** `mqlab vm create rdqm-b` (3 fresh RHEL from the cached box, ~10 min,
   no 2-h rebuild) → `mqlab vm provision rdqm_dr` (install + lab-hosts + firewall +
   mqweb + HA group per site; `failed=0` on all 6).
2. **Build the DR/HA QM.** Deleted the Plan-B HA QMRDQM on site A (reproducible; the
   #218 golden preserves it), then `rdqm-dr-qm-create.sh` created QMRDQM as DR/HA.
3. **Forced cutover.** `virsh destroy lab_rdqm-a{1,2,3}` (hard power-off — domains and
   disks persist, **not** undefined) → `rdqm-dr-force.sh` → QMRDQM `Running` on B.
4. **Failback.** `virsh start lab_rdqm-a{1,2,3}` → `rdqm-dr-cutover.sh b2a`.

## Findings (data vs judgment)

- **Data — DR/HA `crtmqm` is secondaries-first per site, not auto-fan-out.** The first
  attempt (single `-sx` per site, trusting the IBM worked-example's "Secondary queue
  manager created on…" text) failed: `AMQ3812E` / *"the secondary queue manager must
  first be created on the other replicated data nodes"*. The working sequence is
  `-sxs` on the other two nodes **then** `-sx` on the primary — four `crtmqm` total
  (a2/a3→a1, b2/b3→b1) — the same order as the HA create. *(Fixed in
  `rdqm-dr-qm-create.sh`; spec/plan updated.)*
- **Data — after `rdqmdr -p`, the HA subsystem starts the QM itself.** A manual
  `strmqm` on the promoted site fails with `AMQ3681E` ("HA subsystem is already
  managing"). *(Fixed `rdqm-dr-force.sh` to poll for `Running` instead of `strmqm`.)*
- **Data — `dltmqm` does not fan out** (unlike `crtmqm`): deleting a replicated QM
  requires `dltmqm` on each node (RDQM prints the exact per-node command to run).
- **Data — the lab RHEL box ships `firewalld` inactive**, so there are no ports to
  open; the substrate's firewall step is guarded to no-op unless firewalld is running
  (and to install IBM's sample `rdqm-drbd`/`rdqm-mq` service XMLs when it is).
- **Judgment — single FIP per HA group (#223) does not bind DR.** Each site has its
  own floating IP (`10.10.1.100` / `10.10.2.100`); across sites that is two FIPs,
  exactly like the Pacemaker arm's two site VIPs. The cutover moves the active QM
  between sites; clients use a CONNAME list of both FIPs.

## Failback

- **Data — a forced cutover leaves a dual-primary when the old site returns.** Site A
  was killed without demotion, so on power-up it came back still claiming **DR
  primary** while B (force-promoted) was also primary — A showed DR status `Unknown`,
  B `Remote unavailable`. The fix is the canonical "make the former primary the
  secondary": `rdqmdr -m QMRDQM -s` on A, after which A resynced from B and **both
  sites reached DR `Normal`** within ~15 s.
- **Judgment — the DR scripts need a reconcile step.** `rdqm-dr-force.sh` promotes the
  recovery site but does not handle the returned old-primary; an operator (or a future
  `rdqm-dr-reconcile.sh` / a `--reconcile` step) must demote it. The Pacemaker arm's
  forced-DR drill addresses the same hazard (split-brain on the survivor). *Captured as
  a follow-up.*
- **Failback** then is a normal controlled cutover: `rdqm-dr-cutover.sh b2a` (`rdqmdr
  -s` on B, `rdqmdr -p` on A) — the HA subsystem stopped the QM on B and started it on
  A; QMRDQM `Running` on A in ~20 s, both sites DR `Normal`.

## RPO (deferred — next step)

A `pymqi` client put to `HA.TEST` via the FIP returned `MQRC_CALL_INTERRUPTED` (2549)
under `MQCNO_RECONNECT` — a client-side reconnect-during-put wrinkle to resolve before
the data-loss drill. Once the load path is clean, the measured drill is: burst
persistent messages to `HA.TEST` under `wan-degrade` (netem on the active primary's
net-wan egress) → hard power-off site A → force-promote B → `RPO = put − survived on
B`. Qualitatively, RPO is bounded by the async replication lag (widened deliberately
by netem). *(`amqsput` is not installed on the RDQM nodes — the sample tools package
is absent — so the load driver must use `pymqi` from app-client or a small put helper.)*

## Tooling corrected this drill

`rdqm-dr-qm-create.sh` (secondaries-first), `rdqm-dr-force.sh` (no manual `strmqm`),
`site-rdqm-dr.yml` (firewalld-guarded). The DR cutover/failback verbs (`rdqmdr -p/-s`)
and `wan-degrade.sh` (netem) are confirmed on the live group.
