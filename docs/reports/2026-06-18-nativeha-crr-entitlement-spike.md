# Phase-0 CRR-entitlement spike — findings

> **Issue:** #246 (Phase 0). **Date:** 2026-06-18.
> **Verdict:** ✅ **GO** — Native HA **and** CRR are entitled under MQ Advanced
> for Developers 9.4.5 on RHEL 9.6 x86. The Native HA arm is unblocked.
> **Substrate:** reused `rdqm-*` x86 RHEL 9.6 slots (TCG), MQ 9.4.5 Advanced for
> Developers; arm64 pcmk stack left up (parallel dashboard dev).

## Task 1 — Native HA group on RHEL (base MQ, plaintext) — ✅ GO

A 3-node Native HA group `nhaspike` formed on `rdqm-a1/a2/a3` from a **base MQ
install (no RDQM stack)** via the throwaway `mq-nativeha-spike` role.

```
dspmq -m nhaspike -o nativeha -x
QMNAME(nhaspike) ROLE(Active) INSTANCE(rdqm-a1) INSYNC(yes) QUORUM(3/3) GRPROLE(Live)
 INSTANCE(rdqm-a1) ROLE(Active)  REPLADDR(10.50.0.31) INSYNC(yes) HASTATUS(Normal)
 INSTANCE(rdqm-a2) ROLE(Replica) REPLADDR(10.50.0.32) INSYNC(yes) HASTATUS(Normal)
 INSTANCE(rdqm-a3) ROLE(Replica) REPLADDR(10.50.0.33) INSYNC(yes) HASTATUS(Normal)
```

**Finding:** Native HA itself is **entitled** under MQ Advanced for Developers on
RHEL 9.6 x86-64 — quorum 3/3, automatic leader election, all replicas in sync.
The QM defaults to `GRPROLE(Live)` before any CRR config.

**Notes / lessons (pre-applied forward):**
- Base-MQ-only install works for Native HA (dropped all RDQM/DRBD/kmod/drbdpool
  steps from the install role). MQ level asserted ≥ 9.4.4 CD before formation.
- Lifecycle is `mqmonitor@nhaspike` systemd units (active on all three); the @
  unit ships in `MQSeriesSamples`.
- Replication ran on the data IPs `(9414)` for the spike (single network).
- **Infra one-offs fixed** (not plan issues): the RHEL DVD ISO had to be staged
  into the libvirt pool (`/var/lib/libvirt/images/`, qemu-readable); stale
  `lab_rdqm-a*` pool volumes from a failed attempt had to be cleared; and a role
  bug (`lookup('file')` reads the controller, not the remote) was fixed by
  rendering the `NativeHAInstance` block inline from inventory.

## Task 2 — CRR cross-region replication (THE GATE) — ✅ GO

A second 3-node group (`rdqm-b1/b2/b3`, also `QUORUM(3/3)`) was paired to the
Live group as the **Recovery** group. With CRR configured (`NativeHALocalInstance`
group fields + `NativeHARecoveryGroup`), both sites report the cross-region link
**up and synchronized**:

```
# Live site (rdqm-a1):
 GRPNAME(Live)     GRPROLE(Live)     GRSTATUS(Normal) GRPVER(9.4.5.0)
 GRPNAME(Recovery) GRPROLE(Recovery) CONNGRP(yes) INSYNC(yes) BACKLOG(0) GRSTATUS(Normal)
# Recovery site (rdqm-b1): mirror image, CONNGRP(yes) to the Live group
```

5 persistent messages put to `SPIKE.Q` on Live (`CURDEPTH(5)`); Recovery stays
`INSYNC(yes) BACKLOG(0)`.

**Finding (the gate):** **CRR is entitled** under MQ Advanced for Developers
9.4.5 — no licensing rejection; the two groups connected and replicated. The
Native HA arm (#246) is unblocked.

**Bonus finding — CRR runs without TLS.** To isolate the entitlement question
from a GSKit issue (below), CRR was configured **plaintext** (no `CipherSpec`,
no keystore) and it **works** — cross-region groups connect, sync, and replicate.
So TLS is *wanted for security* but is **not required for CRR to function**. (IBM's
docs always show TLS; this proves it isn't a hard functional dependency.)

**⚠️ Lesson to pre-apply to Phase 1/3 — GSKit ships un-extracted.** `runmqakm`
(and `runmqckm`) fail with *"Failed to dlopen ICU library"* because 9.4.5 ships
**GSKit 9 as tarballs** (`/opt/mqm/gskit9/gskssl{32,64}.tar.gz`) that the base
rpm install does **not** unpack — so no ICU libs, and `runmqckm` isn't even on
disk. Base QM ops (`crtmqm`/`strmqm`) don't need GSKit, so Task 1 was unaffected.
**Phase 3 (real TLS, consuming `lab-pki`) must extract/initialize GSKit first** —
a concrete build task surfaced early, exactly what the spike is for.

## Task 3 — planned switchover (message-survival proof) — in progress
