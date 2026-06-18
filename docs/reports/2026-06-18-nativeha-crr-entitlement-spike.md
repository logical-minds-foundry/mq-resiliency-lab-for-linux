# Phase-0 CRR-entitlement spike — findings

> **Issue:** #246 (Phase 0). **Date:** 2026-06-18.
> **Verdict:** _in progress_ — Task 1 (Native HA) GO; Task 2 (CRR gate) pending.
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

## Task 2 — CRR with lab-pki TLS (THE GATE) — pending

Recovery group (`rdqm-b1/b2/b3`) bring-up + base MQ, then `lab-pki` certs on the
RHEL nodes, CRR config (`NativeHALocalInstance` group fields +
`NativeHARecoveryGroup`), enable, and a persistent-message replication proof.
The verdict — does the dev entitlement permit **CRR** — lands here.

## Task 3 — planned switchover — pending
