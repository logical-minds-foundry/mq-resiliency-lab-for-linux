# Lab snapshot / restore — design (#218)

**Date:** 2026-06-16
**Status:** design + MVP spike (RDQM distributed set)
**Issue:** #218 — get back to a provisioned state fast (clone + cheat-bootstrap)

## Problem

Building a lab arm from cold is slow: the RHEL box build runs ~2 hours under TCG
emulation, and provisioning each arm adds more. The box cache (`build/boxes/*.box`,
#57) removes the box-build cost on rebuild but **not** the boot + full provision. We
want to capture the **built + provisioned, message-passing** state and restore it in
minutes, skipping provision entirely — and have that capture survive even an
ephemeral Vergil-VM rebuild.

## Where the state lives (the key constraint)

- The running VMs' disks are libvirt volumes in **`/var/lib/libvirt/images/`** —
  this is **inside the ephemeral Vergil VM**. A VM rebuild wipes it: that is exactly
  why a rebuild re-incurs the 2-hour cost.
- The **project tree (and `build/`) is mounted from the host** (Lima/virtiofs;
  `df build/` → the host mount, multi-TB). Anything under `build/` is **durable
  across a Vergil-VM rebuild**.

So the mechanism is: **copy the provisioned disks out of the ephemeral pool into
host-durable `build/snapshots/`, as self-contained goldens; restore recreates the
domains from those goldens without provisioning.**

## Disk / domain shape (observed)

Per VM (e.g. `lab_rdqm-a1`):
- `vda` — root, qcow2 (`lab_<vm>.img`), a **qcow2 overlay on the vagrant box base**
  image. ~2.7 GB used (RHEL + MQ + config).
- `vdb` — optional extra disk, standalone qcow2 (RDQM drbdpool ~70 MB; pcmk SAN on
  `san-a`). Present only on some nodes.
- `hda` — cdrom = the RHEL DVD ISO (shared, static, already cached). **Not** captured;
  re-attached on restore.
- 5–6 NICs with **fixed MACs** on named libvirt networks (`net-mgmt`, `net-data-a`,
  `net-hb-a`, `net-wan`, `net-ext`, plus vagrant's mgmt net). The networks are
  **persistent + autostart** and survive teardown.

## Mechanism

**Flatten, don't chain.** `qemu-img convert -O qcow2 <src> <golden>` produces a
**standalone** golden — it resolves the overlay→box-base chain into one file. This
removes the dependency on the box base image (whose pool filename carries a build
timestamp and would differ after a rebuild). Goldens are immutable.

**Snapshot** (`lab-snapshot.sh <key> [domains…]`):
1. `virsh shutdown` each domain cleanly (releases the qcow2 write lock; flushes FS /
   MQ logs / DRBD for a consistent capture). Poll to "shut off".
2. For each `device='disk'` (skip cdrom): `qemu-img convert -O qcow2` the pool volume
   → `build/snapshots/<key>/<domain>/<target>.qcow2` (host-durable golden).
3. Save `virsh dumpxml --inactive <domain>` → `<domain>/domain.xml` (clean,
   redefine-able config: disks, NIC MACs, machine/emulator, no runtime vnet/portid).
4. Write a small `manifest.json` (pool paths per target, cdrom path).
5. `virsh start` each domain again — the lab is left running.

**Restore** (`lab-restore.sh <key>`):
1. For each domain in the snapshot: `virsh destroy` + `virsh undefine` (tolerant) to
   tear down any existing instance.
2. Copy each golden back into the pool at its recorded path (a full copy — a few GB,
   ~1–2 min for the whole set; a qcow2-COW overlay on the golden is a future
   optimisation but couples the pool to the build mount).
3. `virsh define` the saved `--inactive` XML verbatim, then `virsh start`. **No
   provision.** No XML rewrite is needed: the inactive config carries no
   `<backingStore>` (libvirt probes the backing from the qcow2 header at start) and the
   restored golden is a standalone flattened qcow2, so defining what we captured is
   both correct and faithful (NIC MACs, cdrom path, machine type all preserved).

**Teardown** is `virsh destroy`/`undefine` of the lab domains; goldens in
`build/snapshots/` persist, so restore is a clone-and-start.

## Addressability (§3.5)

Goldens live under the **main worktree's** host-durable `build/snapshots/<key>/`
(stable across worktrees; a worktree may be removed). `<key>` encodes the slot —
e.g. `distributed-rdqm-rhel-<commit>-<date>` — so a run is recall-able as "this
tooling / this config / this commit / this date", and arms occupy independent slots
(RDQM in one, Pacemaker in another), each restore-able on its own. This extends the
box cache (#57) to the **provisioned** state and the Vagrant-state consolidation
(#167).

## Acceptance — met (2026-06-16)

From the captured `distributed-rdqm-rhel` slot: all 5 domains were torn down
(`virsh destroy` + `undefine`), `lab-restore.sh` cloned the goldens back and
redefined them from the saved XML, and `e2e-test.sh 5 QMRDQM '10.10.1.100(1414)'`
returned **5/5 round-trips OK** — with **no provision step**. End to end the restore
replaced a ~2-hour cold build+provision with a ~10-minute clone+boot.

**Recovery-time note.** After a *clean restart* (snapshot's own halt→start) RDQM HA
brought QMRDQM back in ~45 s. After a *restore* (force-teardown → redefine → boot
off freshly-placed goldens) the QM auto-recovered but took several minutes — DRBD
re-handshakes / resyncs across the three nodes under TCG before Pacemaker starts the
QM. It recovers unattended; a future `restore --wait` could poll `rdqmstatus` until
`Queue manager status: Running` so the command returns only once the lab is live.

## Scope / follow-ups

- **MVP (this issue):** shell spike (`lab/scripts/lab-snapshot.sh` /
  `lab-restore.sh`) proving the mechanism on the RDQM distributed set
  (`rdqm-a1/2/3`, `svc-sim`, `app-client`). Captures the exact working state reached
  by the #216 run.
- **Productionise:** fold into `mqlab lab snapshot|restore <setup>` (Typer command,
  vrg-validate'd, 100% coverage) so it's glass-box and arm-keyed; the disk/XML
  orchestration that is qemu-img/virsh-heavy stays in a helper the command shells to.
- **Pacemaker arm + whole-lab** capture (same mechanism; just more domains).
- **COW-overlay restore** as a speed optimisation once the copy baseline is proven.
- TCG = functional only; no timing claims.
