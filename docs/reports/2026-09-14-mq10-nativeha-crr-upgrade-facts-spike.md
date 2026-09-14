# IBM MQ 10.0 Native HA CRR Upgrade Facts + Media — Spike Findings

**Date:** 2026-09-14
**Issue:** #1070 (epic logical-minds-foundry/.github#219 — MQ 9.4.5 → 10.0 upgrade)
**Task:** Plan Task 0 (spike). Pin the authoritative 10.0 Native HA / CRR upgrade
facts and confirm the developer media, so Task 3 (runbook, #1073) and Task 5 (10.0
version bump, #1075) build on verified behaviour, not assumptions.

**Status:** DECIDED — all facts below are cited to IBM Docs pages actually cached
under `build/refs/ibm-docs/ibm-mq/10.0.x/` on 2026-09-14 (product code
`SSYHRD_10.0.0`). No fact in this note is asserted from memory or from a search
snippet. Facts are applied forward.

**Verdict:** GO. IBM MQ 10.0.0.0 LTS developer media is published and downloadable;
the 9.4.5.0 developer media is already staged; the full 10.0 CRR upgrade doc set is
cached; and the within-group / cross-site ordering, the `dspmq` gates, and the
point-of-no-return / back-out mechanism are all pinned from primary sources.

---

## 1. Media facts

### 1.1 IBM MQ 10.0 developer tarball name/URL — CONFIRMED

IBM MQ **10.0.0.0** LTS is generally available (GA 2026-06-16). The no-charge
"IBM MQ Advanced for Developers" tarballs live in the same public directory and
follow the same naming scheme as the 9.4.5.0 media that `scripts/fetch-mq.sh`
already fetches. Base directory (unchanged):

```
https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/
```

The three artifacts that map onto the two arms `fetch-mq.sh` builds (Ubuntu host-arch
+ x86_64 RHEL/RDQM) were confirmed live with HTTP `HEAD` requests on 2026-09-14:

| Tarball (`<VER>-IBM-MQ-Advanced-for-Developers-<SUFFIX>.tar.gz`) | HTTP | Content-Length | Last-Modified |
|---|---|---|---|
| `10.0.0.0-…-UbuntuLinuxX64.tar.gz`   | 200 | 562,084,800 | Tue, 16 Jun 2026 |
| `10.0.0.0-…-UbuntuLinuxARM64.tar.gz` | 200 | 518,246,431 | Tue, 16 Jun 2026 |
| `10.0.0.0-…-LinuxX64.tar.gz`         | 200 | 560,735,114 | Tue, 16 Jun 2026 |

So for Task 5 the only change `scripts/fetch-mq.sh` needs is `VER="10.0.0.0"`; the
`UbuntuLinux{X64,ARM64}` / `LinuxX64` suffix logic and the base URL are unchanged.
A `10.0.0.5` CD/fixpack level also exists in the same directory — the epic targets
the `10.0` **LTS** base, i.e. `10.0.0.0`. **This spike does not edit `fetch-mq.sh`;
the version bump is Task 5's deliverable.**

> No IBM entitlement/license artifact is committed and none is needed — this is the
> no-charge developer edition on IBM's public mirror. Media bytes stay in the
> gitignored `build/cache/mq/`.

### 1.2 9.4.5 developer tarball staged — CONFIRMED

`mqlab build path cache` → `…/build/cache`; the `mq/` sub-bucket holds both 9.4.5.0
developer tarballs plus their recorded checksums:

```
build/cache/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz        (545,092,589 B)
build/cache/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz.sha256
build/cache/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz  (547,082,320 B)
build/cache/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz.sha256
```

The 9.4.5.0 **source** edge (the version we upgrade *from*) is present and
checksum-guarded on this x86_64 host.

---

## 2. Tooling change made by this spike

`tools/ibm_doc_cache.py` could not fetch **any** `ibm.com/docs` page — including
pages that previously worked — returning HTTP 403. IBM's edge tightened its
anti-bot heuristic: a lone `User-Agent` is no longer sufficient. A request is now
waved through only when the browser-shaped header triad is all present: a real
`Accept`, an `Accept-Language`, **and** an `Accept-Encoding`. The tool sent only
`User-Agent` + `Accept: text/html`.

Fix (this branch): send the full triad, requesting `Accept-Encoding: identity` so
urllib still receives an uncompressed body with nothing to decompress. After the
fix, all twelve 10.0.x pages below cached cleanly. Without this fix Task 0 could not
have been completed at all, so the change is in-scope for the spike.

---

## 3. Cached authoritative sources

All cached under `build/refs/ibm-docs/ibm-mq/10.0.x/<slug>/` (gitignored — IBM
content is not redistributed). Cite `content.txt`; `source_url` is in `meta.json`.

| Slug | Topic |
|---|---|
| `configuring-high-availability-disaster-recovery` | HA/DR landing — Native HA vs CRR vs IRR vs RDQM taxonomy |
| `recovery-native-ha-crr` | Native HA CRR overview (roles, pending transitions, rebase, storage) |
| `crr-upgrading-native-ha-configurations` | **Upgrading a Native HA CRR configuration** |
| `irr-upgrading-native-ha-configurations` | Upgrading a Native HA IRR configuration (in-region sibling) |
| `failover-complete-planned-switchover-native-ha-crr-configuration` | Planned switchover (reversible, zero data loss) |
| `failover-complete-unplanned-native-ha-crr-configuration` | Unplanned failover (possible data loss) |
| `crr-resolving-partitioned-split-brain-problem` | Split-brain resolution |
| `reference-dspmq-display-queue-managers` | `dspmq` reference incl. `-o nativeha` field definitions |
| `linux-migrating-queue-manager-later-version-aix` | QM migration: single-stage / side-by-side / multi-stage |
| `linux-migrating-aix-single-stage` | Single-stage (in-place) migration procedure |
| `mq-upgrading-installation-linux` | Upgrading an MQ installation on Linux (landing) |
| `linux-upgrading-mq-installation-ubuntu-using-apt` | Ubuntu apt upgrade |
| `umil-upgrading-mq-installation-linux-red-hat-using-dnf` | RHEL dnf upgrade |

**Terminology pinned (from `configuring-high-availability-disaster-recovery` and
`recovery-native-ha-crr`):** In 10.0 a Native HA *group* is three log-replicating
instances (one active). A **CRR** (Cross-Region Replication) configuration pairs a
**Live** group with a **Recovery** group in *different regions* over asynchronous
replication for DR. **IRR** (In-Region Replication) is the same Live/Recovery model
for two data centres in *one* region (and is **not** supported in containers). The
lab arm is CRR. Both are distinct from RDQM.

---

## 4. Upgrade ordering

### 4.1 Within a group — upgrade all three instances together

From `crr-upgrading-native-ha-configurations` (`content.txt`): "you should aim to
upgrade all your instances in a group as closely together as possible. **Do not
adopt the cautious approach of updating one node, testing, and then updating the
next node.** If you proceed in this way, you will limit the availability of your
queue manager." The three instances are to be regarded "as together constituting a
single queue manager". So the per-group procedure is: `endmqm` on that whole group,
upgrade all three nodes, then `strmqm` — not a rolling node-by-node upgrade.

> Contrast: the IRR sibling doc (`irr-upgrading-native-ha-configurations`) uses the
> softer wording "upgrade both groups as closely together as possible" and adds a
> `SyncConsistency=Strict` caveat (a Strict Live instance stops if it cannot
> replicate to Recovery). The **CRR** doc — our arm — does not require the
> `SyncConsistency` dance for the switchover path.

### 4.2 Across sites — Recovery group first (version-ordering rule)

From `crr-upgrading-native-ha-configurations`: "all instances in the group
performing the recovery role must be at an IBM MQ version **equal to or higher than**
the group performing the live role. **You cannot fail over or switch over to a group
that is a lower version.** Because of this, you should apply upgrades to all members
of the **recovery group before** applying it to all members of the live group. The
upgrade of IBM MQ is only complete when all instances of both the recovery and live
groups have been upgraded."

Two supported sequences:

1. **Outage upgrade (no switchover):** `endmqm` Recovery group → upgrade all
   Recovery nodes → `endmqm` Live group → upgrade all Live nodes → `strmqm`
   Recovery → `strmqm` Live. QM is unavailable for the whole window.
2. **Zero-/low-downtime upgrade (planned switchover):** `endmqm` Recovery group →
   upgrade all Recovery nodes → planned switchover *to* the now-upgraded (higher)
   group → upgrade the original Live group → switch back. QM is unavailable only
   during the switchovers.

Both obey the same invariant: **the Recovery group is always at a version ≥ the Live
group.** A fixpack (the `F` in `V.R.M.F`) does not force this ordering, but IBM still
recommends upgrading all instances together.

---

## 5. `dspmq` status gates

The command is **`dspmq -o nativeha -g`** (group view). Field definitions are from
`reference-dspmq-display-queue-managers/content.txt`; the field values are also
visible in the repo's own live CRR spike output (`docs/reports/2026-06-18-nativeha-crr-entitlement-spike.md`).

Gate fields, per group:

- **`GRPROLE`** — one of `Live`, `Recovery`, `Pending live`, `Pending recovery`,
  `Unknown`, `Not configured`. The `Pending …` states are the transient
  synchronising states seen during a switchover.
- **`GRSTATUS`** — `Normal`, `Checking`, `Synchronizing`, `Rebasing`,
  `Waiting for connection`, `Disconnected`, **`Partitioned`** (split-brain),
  `Sync failed` (a synchronous-replication Live group currently out of sync),
  `Unknown`. **Gate: proceed only when `GRSTATUS(Normal)`.**
- **`CONNGRP`** (shown for the *remote* group) — `yes` / `no` / `unknown` /
  `suspended`. `suspended` flags a config incompatibility, e.g. a version mismatch
  between groups. **Gate: `CONNGRP(yes)` before any switchover.**
- **`INSYNC`** — whether the group could become Live with **no** data loss. Shown
  for a group when `GRPROLE` is `Recovery` / `Pending live` / `Unknown`. **Gate for a
  planned switchover: `INSYNC(yes)` (with `BACKLOG(0)`).**
- **`BACKLOG`** — KB the group is behind the Live group (shown when `GRPROLE` is
  `Recovery` or `Pending live`).
- **`RCOVLSN` / `RCOVTIME`** — the log sequence number / ISO-8601 time the group
  could recover to. Any data written after this point may be lost on an *unplanned*
  failover. Both feed `dmpmqlog` (`-s` / `-t`/`-u`) for forensic log comparison.
- **`GRPVER`** — the version of the current group leader; the direct check for the
  §4.2 "Recovery ≥ Live" invariant.

Per-instance (non-`-g`) fields include `ROLE`, `INSTANCE`, `INSYNC`, `QUORUM`
(e.g. `3/3`), `GRPLSN`, `GRPNAME`, `GRPROLE`.

**Runbook gate summary (Task 3):** before each step, `dspmq -o nativeha -g` must show
the group at `GRSTATUS(Normal)`, the remote at `CONNGRP(yes)`, and — before a planned
switchover — the target Recovery group at `INSYNC(yes) BACKLOG(0)`, with `GRPVER`
confirming the target is at a version ≥ the current Live group.

---

## 6. Point-of-no-return and the true back-out mechanism

There are **two** distinct point-of-no-return questions in this upgrade; keep them
separate in the runbook.

### 6.1 The queue-manager migration point-of-no-return (per node)

From `linux-migrating-aix-single-stage/content.txt`: "**Back up your system before
you install a later version of IBM MQ over an earlier version. After you start a
queue manager, you cannot revert to the previous version.** If you must restore the
system, you cannot recover any work, such as changes to messages and objects,
performed by the later version of IBM MQ."

So the point-of-no-return is the **first `strmqm` under 10.0** on a given node's
queue-manager data: that data is upgraded in place and cannot be run again under
9.4.5. The **true back-out is restore-from-backup** — a pre-upgrade backup of the
queue-manager data (and system), taken while still on 9.4.5, per "Backing up and
restoring IBM MQ queue manager data". Any work done under 10.0 after that backup is
forfeit on a restore. There is no in-place downgrade.

Deferring the point-of-no-return: **side-by-side** migration
(`linux-migrating-queue-manager-later-version-aix/content.txt`) installs 10.0
alongside 9.4.5 and keeps queue managers associated with 9.4.5 until you explicitly
stop them, uninstall the old version, and migrate — letting you install and verify
10.0 first. Single-stage (in-place) hits the point-of-no-return as soon as the group
restarts under 10.0.

### 6.2 The CRR switchover/failover point-of-no-return (per cutover)

- **Planned switchover** (`failover-complete-planned-switchover-native-ha-crr-configuration`):
  the safe, reversible path. Requesting the switch puts both groups into a `Pending`
  status while recovery logs synchronise; only when synchronised do the groups adopt
  their new roles and elect a new active. The doc states the system "takes steps to
  ensure that you do not lose data or encounter a partitioned (split-brain) problem",
  and "you can switch back … by repeating the process." **RPO zero, reversible.** This
  is the mechanism the low-downtime upgrade (§4.2 seq. 2) relies on.
- **Unplanned failover** (`failover-complete-unplanned-native-ha-crr-configuration`):
  the point-of-no-return path. Because replication is **asynchronous**, a Recovery
  group promoted after the Live site is lost may be behind — "data is lost when an
  unplanned failover occurs." Before triggering it, read `RCOVLSN`/`RCOVTIME` on the
  Recovery group to see exactly how far back it can recover. Promotion sets
  `GroupRole=Live` **and** `Enabled=False` on the `NativeHARecoveryGroup` stanza so
  the promoted group will not try to replicate to the dead site. If the old Live site
  later comes back still in a Live role, you get **two** active instances →
  `GRSTATUS(Partitioned)`, replication suspended, and you must discard one group's
  data before rejoining (`crr-resolving-partitioned-split-brain-problem`: choose which
  group to keep, use `dspmq -o nativeha -g` `INITLSN`/`INITTIME` + `dmpmqlog` to
  compare logs, then set the discarded group to `Recovery` and delete its persistent
  data).

**Lab/VM note:** the switchover/failover docs illustrate role changes with container
`oc`/`kubectl` commands, but the underlying mechanism they describe is the `qm.ini`
edit the VM arm uses directly — `GroupRole` in the `NativeHALocalInstance` stanza and
`Enabled` in the `NativeHARecoveryGroup` stanza — followed by `endmqm`/`strmqm` and
verified with `dspmq -o nativeha -g`. This matches the live behaviour already recorded
in the Phase-0 CRR entitlement spike.

---

## 7. Forward implications

- **Task 3 (runbook, #1073):** the ordering (§4), the `dspmq` gates (§5), and the
  two point-of-no-return / back-out mechanisms (§6) are the runbook's spine. Use the
  planned-switchover path for low downtime; take a verified 9.4.5 queue-manager-data
  backup **before** the first `strmqm` under 10.0 as the only true back-out.
- **Task 5 (10.0 version bump, #1075):** change `scripts/fetch-mq.sh` `VER` to
  `10.0.0.0`; suffixes and base URL are unchanged (§1.1). All three tarballs are
  live.

---

## 8. Blockers / open items

- **None blocking.** All required facts came from `ibm.com/docs` pages the repo tool
  can now cache, and all three 10.0.0.0 developer tarballs are downloadable.
- **Not fetched (by design, not needed):** the IBM *Support* page
  "Configuring IBM MQ Native HA Cross-Region Replication (CRR) on Linux"
  (`ibm.com/support/pages/node/7261515`, and its `inline-files` PDF) is retrievable
  by neither `WebFetch` nor `tools/ibm_doc_cache.py` (the tool resolves
  `ibm.com/docs/...` only). It is a how-to companion; every authoritative fact in
  this note is sourced from the cached `ibm.com/docs` pages instead. If Task 3 later
  wants that PDF verbatim, it must be fetched manually by a human (download into
  `build/temp/` and read on the box) — flagging here rather than paraphrasing an
  unread source.
