# Native HA on RHEL — Phase 3 (CRR / full HADR) findings

> **Issue:** #246 (Phase 3). **Date:** 2026-06-18. **Arm:** `nativeha-rhel`.
> **Substrate:** 3+3 RHEL 9.6 x86-64 TCG (`nha-rhel-a*` Live / `nha-rhel-b*`
> Recovery), MQ 9.4.5 Native HA + CRR, TLS via `lab-pki`. The #267 distributed-HADR
> keystone. Functional correctness only (TCG; RPO qualitative).

## TLS — solved (the real root cause)

`runmqakm` failed only because the minimal RHEL box lacked the **OS `libicu`**
(`libicuio.so.67`). **`dnf install libicu`** fixes it (`lab-gotchas.md`). Then the
proper path works: deploy the **`lab-pki` PKCS#12** (`.p12`, OpenSSL, CA-signed),
stash its password with `runmqakm -keydb -stashpw -type pkcs12`, set
`NativeHALocalInstance` `CipherSpec=ANY_TLS12` / `CertificateLabel=QMNATIVE` /
`KeyRepository=<.p12 stem>` (`mq-nativeha/tasks/tls.yml`). **Verified:** Native HA
replication negotiates `ECDHE_RSA_AES_256_GCM_SHA384` and the group stays
`QUORUM(3/3) INSYNC` — TLS replication with a CA-signed cert, no GSKit `.kdb`.

## CRR cross-region — ✅

Site-B Recovery group (`QUORUM(3/3)`) paired to the site-A Live group via CRR over
`net-wan`, TLS. Both sites: `GRPNAME(Live) GRPROLE(Live)` + `GRPNAME(Recovery)
GRPROLE(Recovery)`, `CONNGRP(yes)`, `INSYNC(yes)`, `BACKLOG(0)`, `GRPVER(9.4.5.0)`,
`GRPADDR` on `10.99.0.8x` (net-wan). Async cross-region (RPO 0 within a group,
non-zero across).

## DR drills — ✅ (the deliverable)

| Drill | Action | Result |
|---|---|---|
| **Cutover A→B** | `mqlab dr cutover` (flip `GroupRole`, restart) | site B → `GRPROLE(Live) QUORUM(3/3)`; **`DR.TEST` `CURDEPTH(7)` — all 7 persistent messages survived the cross-region cutover** |
| **Failback B→A** | `mqlab dr failback` | site A → Live; `DR.TEST` `CURDEPTH(7)` — survived the round-trip |

7 persistent messages put at Live (A) → replicated by CRR (Recovery `RCOVLSN`
advanced, `INSYNC`) → present on the promoted group after each switch. The
controlled cross-site cutover moves the queue manager **and its committed
messages**, over TLS. (App-flow-across-cutover with client reconnect is the
Plan-4 `dr_mqi.py` job — Phase-1 finding; the data-layer guarantee shown here is
the core.)

`mqlab dr cutover`/`failback` declared as the arm's verbs
(`site-nativeha-switchover.yml`, `target_live`); parity MATRIX `nativeha-rhel`
flipped to SUPPORTED for the proven HA + CRR/DR verbs (diagnostics still NOT_YET).

## Apples-to-apples HADR ledger (vs RDQM-DR / pcmk-DR)

| Dimension | Native HA CRR | RDQM-DR | pcmk-DR |
|---|---|---|---|
| Replication layer | **in-QM raft log** (the QM's own) | DRBD (block, below MQ) | DRBD (block) |
| Cross-site transport | MQ-native, TLS | DRBD async | DRBD async + Booth |
| Switchover | edit qm.ini `GroupRole` + restart | `rdqmdr` | Booth ticket / scripted |
| Substrate to hand-build | **none** (no DRBD/Pacemaker/Booth/STONITH/SAN) | RDQM-bundled (kernel-locked x86/RHEL) | full OSS cluster |
| TLS | standard MQ keystore (lab-pki) | n/a (block layer) | n/a (block layer) |

The whole DR substrate the other arms assemble is **absent** — CRR is the queue
manager replicating its own log across regions.

## Cold-rebuild acceptance gate (3+3) — ✅ PASSED

`vagrant destroy` both groups → 6 fresh nodes → **one** `site-nativeha-dr.yml`
run (`failed=0` ×6) → CRR re-formed **one-pass, no manual fix-ups**: Live
`QUORUM(3/3)`; Recovery `CONNGRP(yes) INSYNC(yes) BACKLOG(0)` over `net-wan`,
`GRPVER(9.4.5.0)`, TLS. The full distributed-HADR substrate (3+3 Native HA + CRR +
lab-pki TLS) is reproducible from scratch — lint-green ≠ done; the cold rebuild
proves it.

## Phase 3 verdict — COMPLETE

The `nativeha-rhel` arm now delivers full HADR: 3-node raft HA per site, TLS
cross-region CRR, message-preserving cutover/failback via `mqlab dr`, all on the
one consolidated `distributed-nativeha-rhel` setup (#267), cold-rebuild-proven.
Remaining for the arm: distributed-mesh-across-cutover with client reconnect
(`dr_mqi.py`, Plan 4) and `diagnostics` (runmqras). Phase 2 (Ubuntu) deferred.
