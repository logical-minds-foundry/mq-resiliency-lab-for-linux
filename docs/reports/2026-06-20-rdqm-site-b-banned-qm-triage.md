# RDQM DR — Triaging a Banned Queue Manager With the Cockpit

**Date:** 2026-06-20
**Issue:** #287 (RDQM cluster cockpit) — live-verification capstone
**Setup:** `rdqm_dr` (RDQM 3+3 — site-A HA group `rdqm_a` + site-B DR group `rdqm_b`, QM `QMRDQM`)

A worked example of the lab's whole thesis: stand up a genuinely complex technology,
surface every layer of it in one cockpit, and use that cockpit to find, explain, and fix a
real fault that you could not have seen from a single command line. The fault here was real
and present on the live cluster; the cockpit is what made it legible.

---

## 1. How it presented (the cockpit symptom)

The `lab-rdqm-cluster` board lit up top-to-bottom with a coherent, drill-downable failure
story — every layer agreeing that **site B could not take over**:

| Panel | Reading |
|---|---|
| ① Integrity | **⚠ HAZARD** (red) |
| ② Instances — Site B chip | **⚠ NOT READY** (red) |
| ② Instances — Site B `Pacemaker` cell | **blocked** (red), on all three b-nodes |
| ③ Pacemaker — Site B `fail-count` | **BANNED** (red) on rdqm-b1/b2/b3; `QM` = stopped |
| ⑤ Cross-site DR — Failover ready | **⚠ NOT READY** (red) |
| Everything site A | green — QM running on rdqm-a1, DR Normal, DRBD in-sync |

Crucially, the **HA layer looked fine**: `rdqmstatus` reported HA status `Normal` on every
site-B node. A human running `rdqmstatus` alone would have concluded site B was healthy. The
failure lived one layer down, in **pacemaker**, and only the cockpit's pacemaker section made
it visible.

## 2. Why it was invisible without the cockpit

RDQM is `rdqmadm`/`rdqmstatus`/`rdqmdr` wrapping **Pacemaker + DRBD (HA) + DRBD (DR)**. Each
layer can be healthy while the one above or below is broken:

- `rdqmstatus` reports the **HA/DR domain** view (HA role/status, DR role, floating IP). It does
  **not** report whether Pacemaker can actually *start* the queue-manager resource. On site B it
  said `Normal`.
- `drbdsetup` reports **storage** health. Both DRBD layers were `Connected`/`UpToDate`.
- **Pacemaker** is where the queue manager resource was *banned* — and nothing in the HA or
  storage views shows that.

The cockpit collector probes all three (`rdqmstatus` + `drbdsetup` + `crm_mon`) and renders
them as separate technology layers, so the divergence — HA `Normal` but the QM resource
`BANNED` — is visible at a glance. This blind spot is exactly why the pacemaker section was
added (it had been missing in the first cut of the board).

## 3. Discovery — the CLI walkthrough

The cockpit pointed at the layer; the command line confirmed the cause. Run on a site-B node.

**(a) A surprise up front — `pcs` is not installed.** RDQM manages Pacemaker itself; you do
*not* triage it with the `pcs` tooling you'd reach for on a vanilla RHEL cluster:

```console
$ pcs status
/bin/sh: line 1: pcs: command not found
```

Use the Pacemaker CLI directly: `crm_mon`, `cibadmin`, `crm_resource` — plus RDQM's own
`rdqmstatus` / `rdqmdr`.

**(b) The fail-count — `crm_mon`.** The queue-manager resource is `Stopped` everywhere on
site B and **banned at INFINITY** on all three nodes:

```console
$ crm_mon --one-shot --output-as=xml   # qmrdqm fail-counts
  rdqm-b1: fail-count=1000000   (pacemaker INFINITY = migration threshold reached)
  rdqm-b2: fail-count=1000000
  rdqm-b3: fail-count=1000000

Failed Resource Actions:
  * qmrdqm start on rdqm-b3 returned 'error' ... after 1.028s   (Fri Jun 19 18:22:29)
  * qmrdqm start on rdqm-b1 returned 'error' ... after 1.111s   (Fri Jun 19 18:21:45)
  * qmrdqm start on rdqm-b2 returned 'error' ... after 1.024s   (Fri Jun 19 18:23:06)
```

**(c) Why the start fails — `cibadmin`.** The queue manager's filesystem is backed by the
**cross-site DR** DRBD volume:

```console
$ cibadmin --query --scope resources | grep -A3 p_fs_qmrdqm
  <primitive id="p_fs_qmrdqm" ... type="Filesystem">
    <nvpair name="device" value="/dev/drbd/by-res/qmrdqm.dr/0"/>
    <nvpair name="directory" value="/var/mqm/vols/qmrdqm"/>
```

`qmrdqm.dr` is the DR-replicated volume. On the DR **secondary** (site B) it is the read-only
**Secondary** — data flows A→B — so it cannot be mounted locally, so the QM cannot start, so
the start op errors in ~1 s. That is correct, by design: a DR-secondary site cannot run the
queue manager until it is promoted (`rdqmdr -p`).

## 4. Root cause

The site-B nodes carried **stale INFINITY fail-counts** for `qmrdqm`, left over from
queue-manager start attempts on **Jun 19 18:21–18:23** (during DR setup / a forced-DR
exercise). In steady state Pacemaker correctly scores site B as *ineligible* to run the QM (its
DR volume is the read-only Secondary) and leaves the resource cleanly `Stopped`. But the old
INFINITY fail-counts persisted and kept the resource **banned**, which the cockpit faithfully
rendered as HAZARD / NOT READY / BANNED.

So the dashboard was **100% correct**: site B genuinely could not take over a failover in that
state. The fault was a stale Pacemaker ban, not a storage or HA problem — and not anything the
HA-layer `rdqmstatus` view could ever have shown.

## 5. The fix

Clear the stale fail-counts; let Pacemaker re-evaluate. `crm_resource --cleanup` only erases
failure history — it is reversible and starts nothing:

```console
$ crm_resource --cleanup --resource qmrdqm      # run on a site-B node, as root
Cleaned up qmrdqm on rdqm-b1 / rdqm-b2 / rdqm-b3
```

No deeper reconfiguration was needed. We did **not** run `rdqmdr -p` (that would be a real DR
cutover, not a fix) — the goal was to restore the correct *managed-stopped* DR-secondary state.

## 6. Verification — back to green

After the cleanup, Pacemaker re-probed and **left the QM `Stopped` without re-attempting the
start** — proving the ban was stale, not a live inability:

```console
$ crm_resource --cleanup --resource qmrdqm && sleep 20 && crm_mon -1 --output-as=xml
  failcounts: {}            # cleared, and stayed cleared
  qmrdqm role: Stopped      # correct for a DR secondary; no re-fail
```

The collector's 5 s timer carried it straight onto the board (read back from Prometheus):

```
cluster_rdqm_failcount{groups="rdqm_b"}    = 0     (was 1000000)
cluster_rdqm_qm_startable{groups="rdqm_b"} = 1
cluster_rdqm_node_ready{groups="rdqm_b"}   = 1
integrity banned-sites                      = 0
```

Board state restored: site-B chip **RECOVERY (blue — a healthy standby)**, `Pacemaker` =
**ready**, `QM` = **stopped** (normal for a DR secondary), `fail-count` = **ok**, **Integrity
green**, **Failover ready ✓**. Site A stayed green throughout (the live service was never at
risk).

## 7. Lessons

1. **Layered systems hide faults between the layers.** HA `Normal` + DRBD `UpToDate` + QM
   `BANNED` were all true simultaneously. A single command (`rdqmstatus`) gave a false all-clear;
   the cockpit's per-layer rendering is what exposed the truth. This is the case for the board.
2. **RDQM ≠ vanilla Pacemaker for triage.** `pcs` is absent; use `crm_mon` / `cibadmin` /
   `crm_resource` and the `rdqm*` family. (Captured in `docs/reference/rdqm-ha-cheatsheet.md`.)
3. **A DR secondary *should* show its QM stopped.** "Stopped" on site B is the correct,
   green-worthy state — which is why the board paints a standby QM cell neutral, not red, and
   only goes loud on a real ban (fail-count) or a site that genuinely cannot host the QM.
4. **Stale INFINITY fail-counts survive the condition that caused them.** A transient during DR
   setup/cutover can leave a permanent ban; `crm_resource --cleanup` is the routine remedy, and
   the cockpit is what tells you it's needed and confirms it worked.
5. **The cockpit closed the loop end-to-end:** it raised the alarm, localized it to the
   pacemaker layer, and verified the fix — without a single `ssh + grep` guessing session.
