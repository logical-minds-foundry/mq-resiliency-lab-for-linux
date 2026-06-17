# Design: `pcmk-rhel` — a third HA/DR arm (OSS Pacemaker/DRBD on RHEL, non-RDQM)

- **Status:** Draft (brainstorm output, pending review)
- **Date:** 2026-06-17
- **Issue:** #238
- **Supersedes:** nothing — extends the parity work in
  `docs/specs/2026-06-15-rdqm-parity-pivot-design.md` and
  `docs/specs/2026-06-15-rdqm-parity-build-design.md`

## 1. Context

The HA/DR parity work currently compares two arms, registered in
`lab/topology.yaml`'s `arms:` block:

1. **`pcmk-ubuntu`** — open-source Corosync / Pacemaker / DRBD on Ubuntu 24.04,
   with Ubuntu's IBM MQ 9.4.5 build on top. Built from the `pcmk-cluster`,
   `pcmk-stonith`, `drbd-san`, and `mq-pcmk-qmgr` Ansible roles.
2. **`rdqm-rhel`** — IBM RDQM on RHEL 9.6. RDQM *bundles* Pacemaker and DRBD and
   exposes them only through the `crtmqm -sx` / `rdqmadm` / `rdqmint` command
   set. Built from `rdqm-install` and `rdqm-ha`.

The pivot spec records a standardization decision: "the firm standardizes on
RHEL + RDQM, for IBM-supportability reasons."

This design challenges the premise behind that decision. What forced the move to
RHEL was never RDQM — it was that IBM supports MQ only on current RHEL or on
Ubuntu 22 (which the firm has rejected). RHEL does not *require* RDQM. The same
open-source substrate already proven on the Ubuntu arm can run directly on RHEL,
with the IBM-supported RHEL MQ build on top and no RDQM involved.

That is a **third arm** the comparison never tasted. It isolates the operating
system as the only variable against `pcmk-ubuntu`, and it isolates
substrate-ownership as the only variable against `rdqm-rhel`.

## 2. Decision

Add **`pcmk-rhel`** as a first-class third arm, peer to the existing two. The
RDQM arm stays as live contrast data. The firm-standard decision is **deferred
to the evidence** produced by the parity harness; this design does not rewrite
the standardization banner.

Substrate sourcing on RHEL:

- **Pacemaker / Corosync / pcs** — the **RHEL High Availability Add-On**
  (a Red Hat–supported subscription entitlement; not in base RHEL).
- **DRBD** — **ELRepo** (`kmod-drbd` + `drbd-utils`), community-sourced, with the
  kernel module matched to the RHEL 9.6 kernel.

## 3. Architecture

### 3.1 Arm registration

Add a `pcmk-rhel` entry to `lab/topology.yaml`'s `arms:` block, on RHEL 9.6
(x86_64 / TCG — the same box family as the RDQM arm). New setups mirror the
Ubuntu arm's: a `distributed-pcmk-rhel` setup parallel to
`distributed-pcmk-ubuntu`, plus the HA and DR setups parametrized to the arm.

### 3.2 Shared roles + OS-adapter seam

The arm reuses the **same four roles** as `pcmk-ubuntu` — not copies:
`pcmk-cluster`, `pcmk-stonith`, `drbd-san`, `mq-pcmk-qmgr`. Each role's
OS-specific surface is factored into per-family task files
(`tasks/install-Debian.yml` vs `tasks/install-RedHat.yml`) selected by
`ansible_os_family` via `include_tasks`. The cluster-formation, DRBD, VIP, and
MQ-resource *orchestration* stays in shared task files.

This makes parity **structural**: both arms execute the same orchestration, so
"only the OS differs" is enforced by the code rather than asserted in a report.
The known OS-specific surface is narrow:

| Role | Debian/Ubuntu | RedHat/RHEL |
|------|---------------|-------------|
| `pcmk-cluster` | pacemaker/corosync/pcs via `apt` | HA Add-On via `dnf` (enable the add-on repo) |
| `drbd-san` | DRBD + iSCSI tooling via `apt` | ELRepo `kmod-drbd` + `drbd-utils`; iSCSI target/initiator package names differ |
| `pcmk-stonith` | `fence_virsh` (fence-agents) | `fence_virsh` (`fence-agents-virsh`) — agent portable, package name differs |
| `mq-pcmk-qmgr` | MQ 9.4.5 from IBM tar; Pacemaker resource def | identical MQ resource def — the parity proof |

### 3.3 The two RDQM limitations fall out structurally

- **Multiple VIPs.** `mq-pcmk-qmgr` already defines two `IPaddr2` resources
  (data-plane + external partner) on the Ubuntu arm. On `pcmk-rhel` that is the
  same code, so the DMZ multi-VIP topology that RDQM rejects (`AMQ3877E`, one
  floating IP per QM — see #223 and
  `docs/reports/2026-06-17-rdqm-vs-pacemaker-floating-ip.md`) simply works.
- **Add DR after HA.** Because the arm owns DRBD and the cluster directly, adding
  a recovery site is a configuration operation, not a teardown and rebuild —
  refuting the RDQM constraint that DR must be declared at `crtmqm` time.

## 4. Parity definition & evidence

The arm reuses the existing parity harness unchanged — the
`(setup × config × commit) → outcomes` run-report corpus and the capability
matrix. A new `pcmk-rhel` column joins `pcmk-ubuntu` and `rdqm-rhel`.
`pcmk-ubuntu` becomes the **control** (isolates the OS variable); `rdqm-rhel`
becomes the **contrast** (isolates the substrate-ownership variable).

The arm is **real** only when its run-report corpus shows, on a one-pass cold
RHEL 9.6 rebuild:

- **HA** — form-group, intra-site failover, RPO 0 within site, no split-brain.
- **DR** — cross-site replication, cutover, failback.
- **Distributed / DMZ** — the `distributed-pcmk-rhel` topology with two VIPs
  (data-plane + external partner).
- **Limitation refutations (first-class evidence)** — (1) a second VIP added and
  live; (2) a DR recovery site added to an already-running HA cluster without a
  rebuild.

## 5. Support-boundary thesis

The design records, as analysis the pivot spec omitted, where the IBM support
boundary sits per arm:

- **`pcmk-ubuntu` / `pcmk-rhel`** — the boundary sits **at** the MQ layer. The
  cluster substrate is self-supportable: in-house expertise plus the large OSS
  community, and on RHEL, Red Hat backs the HA Add-On. DRBD and Pacemaker
  problems never route through IBM.
- **`rdqm-rhel`** — the boundary sits **below** MQ. IBM owns the bundled DRBD and
  Pacemaker, which are not MQ-specific technologies, so substrate problems route
  through IBM. IBM also exposes only a subset of Pacemaker's capability (one VIP;
  DR-at-creation-only) — evidence of the cost of that lower boundary.

`pcmk-rhel` is the hypothesized "sweet spot": IBM-supported MQ on IBM-supported
RHEL, over a self-supportable substrate. Whether it becomes the firm standard is
**deferred to the evidence**.

## 6. Build sequencing

Sequenced so each layer proves before the next, all under the cold-rebuild
acceptance gate. The full-parity bar still governs "done"; sequencing only
de-risks a large bring-up.

1. Substrate up on RHEL 9.6 — HA Add-On cluster forms; ELRepo `kmod-drbd`
   matches the kernel and DRBD syncs.
2. MQ 9.4.5 as a generic Pacemaker resource (single VIP) — intra-site failover.
3. Multi-VIP DMZ topology (`distributed-pcmk-rhel`).
4. DR site + cutover/failback; then the add-DR-after-HA refutation.

## 7. Risks & open questions

- **ELRepo `kmod-drbd` ↔ RHEL 9.6 kernel matching** — kABI-tracking kmod vs
  kernel updates is the most likely first-rebuild failure; pin and verify.
- **MQ as a generic Pacemaker resource** — confirm the resource model (an
  IBM-provided multi-instance OCF agent vs a systemd/LSB resource) and that it is
  genuinely identical to the Ubuntu arm's definition.
- **STONITH on the RHEL/TCG box** — confirm `fence_virsh` parity with the
  Ubuntu/KVM setup.
- **RHEL HA Add-On repo availability / entitlement** on the lab box.

## 8. Acceptance

Full three-way parity: HA + DR + distributed/DMZ on RHEL, with both RDQM
limitations demonstrably lifted, proven on a one-pass cold RHEL 9.6 rebuild and
captured in the parity run-report corpus as a `pcmk-rhel` column alongside the
existing two arms.
