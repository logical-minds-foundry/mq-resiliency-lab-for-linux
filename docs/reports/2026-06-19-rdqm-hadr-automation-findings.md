# RDQM HA/DR (3+3) Automation — Findings

**Date:** 2026-06-19
**Issue:** #288
**Status:** build path automated + proven live; cutover proven-capable, hardening tracked in #294.

Closes the gap from the Phase-C drill (`2026-06-06-phase-c-rdqm-findings.md`), which *proved*
RDQM HA+DR by hand but never automated the DR half. This makes a 3+3 RDQM HADR build a one-pass,
repeatable operation.

## What's automated now

- **`ansible/site-rdqm-dr.yml`** — provisions BOTH sites' HA groups: imports `site-rdqm.yml`
  (site A, idempotent) + the same roles on `rdqm_b` (site B). `mqlab vm provision rdqm_dr` runs
  it. (Also fixes `mqlab obs instrument rdqm_dr` failing on rdqm-b* — they now have a dnf repo.)
- **`lab/scripts/rdqm-qm-create.sh`** — auto-detects the formed site-B group and creates the
  DR/HA QM across both sites; HA-only path preserved. `mqlab qm create rdqm_dr` runs it.
- **topology** — `rdqm_dr` provisions `site-rdqm-dr.yml` + has a `qm:` block (so `qm
  create`/`status` work).

## Verified live (rdqm_dr, 3+3)

- `mqlab vm provision rdqm_dr`: site-B HA group formed (rdqm-b1/2/3 online), all 6 nodes clean.
- `mqlab qm create rdqm_dr`: QMRDQM created both sites; **DR status Normal on both** —
  site A `DR role Primary` (DR local 10.99.0.31, remote 10.99.0.41/42/43, port 7001), site B
  `DR role Secondary`. HA Normal on all 6 nodes; floating IPs 10.10.1.100 (A) / 10.10.2.100 (B).

## Two corrections the live tool forced (vs. the docs)

1. **DR/HA needs secondaries-first per site.** IBM's 9.4 worked example implies the primary
   `crtmqm -sx -rr p` auto-creates the local secondaries; our MQ 9.4.5 build does NOT — it errors
   "the secondary queue manager must first be created" and prints the `-sxs -rr p -rl/-ri`
   command. Fixed: a2/a3 (`-sxs -rr p`) then a1 (`-sx -rr p`); b2/b3 (`-sxs -rr s`) then b1; DR
   flags (`-rl <local wan> -ri <remote wan> -rp 7001`) on every `crtmqm`. (Committed.)
2. **The exact `crtmqm` DR flags** came from the cached IBM docs
   (`build/refs/ibm-docs/.../availability-creating-drha-rdqms` + worked example), not guesswork:
   `-rl DRLocalIPs -ri DRRemoteIPs -rp Port` (the `-rp/-ra` from an earlier reconstruction were wrong).

## Cutover drill — capability confirmed, automation needs hardening (#294)

`rdqm-dr-cutover.sh a2b` flipped the DR roles correctly (A→Secondary, B→Primary, VIP to
10.10.2.100), but the drill surfaced cutover-script gaps:
- The script hardcodes `rdqm-a1`/`rdqm-b1`, but RDQM placed site-B's QM on **rdqm-b2**, so the
  failback's `rdqmdr -s` on b1 hit `AMQ3705E: not issued on the HA primary node` (exit 2). The
  fix is to run `rdqmdr` on the current HA primary (`HA current location`).
- After `rdqmdr -p`, the QM HA-bounced and Pacemaker blocked it at the new primary
  (`HA blocked: All nodes`) — TCG start/monitor-timing, not a data fault (QM log: warnings + a
  controlled end; node had free RAM). Phase-C cutover succeeded at ~69 s.
- Recovery: `rdqmdr -s` on the real HA primary (rdqm-b2) + `rdqmdr -p` on rdqm-a1 restored
  QMRDQM Running at site A, DR Normal. Service was restored without rebuilding.

All three are tracked in **#294** (HA-primary node discovery, confirm-sync-before-cut, HA timeout
tuning, a `mqlab dr cutover` verb). The cutover *capability* is proven (Phase-C + the role-flip
here); the *script* is a first-pass.

## Net

The RDQM 3+3 HADR build is automated and repeatable (provision → DR/HA QM, DR Normal both sites),
anchored to IBM-sourced commands. Cutover hardening is deferred to #294. Convergence to a single
`distributed-rdqm-rhel-hadr` keystone remains #267.
