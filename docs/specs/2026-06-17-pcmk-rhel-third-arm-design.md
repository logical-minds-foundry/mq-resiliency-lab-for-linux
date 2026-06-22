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

The pivot spec records a standardization decision: "the app standardizes on
RHEL + RDQM, for IBM-supportability reasons."

This design challenges the premise behind that decision. What forced the move to
RHEL was never RDQM — it was that IBM supports MQ only on current RHEL or on
Ubuntu 22 (which the app has rejected). RHEL does not *require* RDQM. The same
open-source substrate already proven on the Ubuntu arm can run directly on RHEL,
with the IBM-supported RHEL MQ build on top and no RDQM involved.

That is a **third arm** the comparison never tasted. Its clean,
single-variable delta is against `rdqm-rhel`: same OS (RHEL 9.6) and same
platform (x86_64 / TCG), with the **only** difference being substrate
ownership — an OSS Pacemaker/DRBD substrate we own versus IBM's bundled,
wrapped substrate. That is the axis the support-boundary thesis turns on, and
this arm makes it rigorous.

Against `pcmk-ubuntu` the comparison is strong but **not** clean: the Ubuntu
arm runs on aarch64 / KVM while this arm runs on x86_64 / TCG to match the
RDQM platform, so CPU architecture and virtualization accel are confounds, not
constants. We treat the OS-vs-Ubuntu comparison as corroborating evidence and
document those confounds rather than claiming OS as the sole variable. (A
single box arch cannot be clean against both an aarch64 Ubuntu arm and an
x86_64 RDQM arm; we choose to be clean against RDQM, the more important axis.)

## 2. Decision

Add **`pcmk-rhel`** as a first-class third arm, peer to the existing two. The
RDQM arm stays as live contrast data. The app-standard decision is **deferred
to the evidence** produced by the parity harness; this design does not rewrite
the standardization banner.

Substrate sourcing on RHEL:

- **Pacemaker / Corosync / pcs** — free, open-source software. Not on the RHEL
  binary DVD, so the lab host-fetches the **RHEL-compatible HighAvailability
  RPMs** (AlmaLinux 9 / Rocky 9 / CentOS Stream 9 — binary-compatible with
  RHEL 9) and serves them offline to the guests; **no subscription required**.
  Red Hat's own HA Add-On is an *optional* paid support layer, not a
  prerequisite for the software.
- **DRBD** — **ELRepo** (`kmod-drbd` + `drbd-utils`), community-sourced, with the
  kernel module matched to the RHEL 9.6 kernel.

## 3. Architecture

### 3.1 Arm registration

Add a `pcmk-rhel` entry to `lab/topology.yaml`'s `arms:` block, on RHEL 9.6
(x86_64 / TCG — the same box family as the RDQM arm). New setups mirror the
Ubuntu arm's: a `distributed-pcmk-rhel` setup parallel to
`distributed-pcmk-ubuntu`, plus the HA and DR setups parametrized to the arm.

### 3.2 Shared roles + OS-adapter seam

The arm reuses the **same roles** as `pcmk-ubuntu` — not copies. The SAN
pcmk arm composes roughly six roles, each with an OS-specific surface:
`pcmk-cluster`, `pcmk-stonith`, `iscsi-target`, `iscsi-initiator`, `drbd-san`,
and `mq-install`, plus the `mq-pcmk-qmgr` Pacemaker resource definitions. Each
role's OS-specific surface is factored into per-family task files
(`tasks/install-Debian.yml` vs `tasks/install-RedHat.yml`) selected by
`ansible_os_family` via `include_tasks`. The cluster-formation, DRBD, VIP, and
MQ-resource *orchestration* stays in shared task files.

This makes parity **structural**: both arms execute the same orchestration, so
the only differences are in the OS-adapter task files, enforced by the code
rather than asserted in a report.

| Role | Debian/Ubuntu | RedHat/RHEL |
|------|---------------|-------------|
| `pcmk-cluster` | pacemaker/corosync/pcs via `apt` | HA Add-On via `dnf` (enable the add-on repo) |
| `iscsi-target` / `iscsi-initiator` | target/initiator tooling via `apt` | RHEL target/initiator packages (`targetcli`/`iscsi-initiator-utils`) |
| `drbd-san` | DRBD via `apt` | ELRepo `kmod-drbd` + `drbd-utils` (kmod matched to the RHEL kernel) |
| `pcmk-stonith` | `fence_virsh` (fence-agents) | `fence_virsh` (`fence-agents-virsh`) — agent portable, package name differs |
| `mq-install` | MQ 9.4.5 `UbuntuLinuxARM64` tar, `.deb` via `apt` | **distinct workstream**: MQ 9.4.5 `LinuxX64` RPM tar via `rpm`/`dnf` (`MQSeriesRuntime`, `MQSeriesServer`, …) |
| `mq-pcmk-qmgr` | Pacemaker resource def (`systemd:` unit) | identical resource def — the parity proof |

**MQ install is the heaviest adapter, not a free one.** It is a different
download (`LinuxX64` RPM tar, not `UbuntuLinuxARM64`) and a different packaging
model (dpkg → rpm), so it is called out as its own workstream. The existing
`rdqm-install` role already installs the base MQ RPMs on RHEL and serves as the
template; only the RDQM-specific steps are dropped.

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
`rdqm-rhel` is the **clean contrast** — same OS and platform, sole variable is
substrate ownership — so it carries the rigorous comparison. `pcmk-ubuntu` is
**corroborating** evidence of the OSS substrate's behavior on a different
OS/arch (ARM64/KVM), with that arch/accel difference noted as a confound, not
treated as a controlled variable.

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
  cluster substrate is self-supportable: app expertise plus the large OSS
  community. The cluster software (Pacemaker/Corosync/pcs) is free OSS
  regardless of distro; on RHEL, vendor support is an *optional* choice — buy
  Red Hat's HA Add-On as a backstop, or self-support — and either way it is
  **not IBM**. DRBD and Pacemaker problems never route through IBM.
- **`rdqm-rhel`** — the boundary sits **below** MQ. IBM owns the bundled DRBD and
  Pacemaker, which are not MQ-specific technologies, so substrate problems route
  through IBM. IBM also exposes only a subset of Pacemaker's capability (one VIP;
  DR-at-creation-only) — evidence of the cost of that lower boundary.

`pcmk-rhel` is the hypothesized "sweet spot": IBM-supported MQ on IBM-supported
RHEL, over a self-supportable substrate. Whether it becomes the app standard is
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
- **MQ Pacemaker resource on RHEL** — the resource model is already settled: the
  Ubuntu arm runs the QM as a `systemd:mq-<qm_name>` resource (not an OCF agent).
  The only open part is confirming the equivalent `systemd` unit is present and
  created the same way on RHEL.
- **STONITH on the RHEL box** — the RHEL guests run under **TCG emulation**, not
  KVM like the Ubuntu arm. `fence_virsh` drives libvirt on the hypervisor
  regardless of accel, so it should port, but fencing an emulated guest warrants
  explicit confirmation; reuse the hypervisor-side `fence_virsh` key
  authorization established in #135.
- **HighAvailability package staging** — the HA packages are free OSS but not on
  the RHEL DVD; host-fetch them from a free EL9-compatible repo (Alma/Rocky/
  Stream 9) into `build/`. No entitlement needed; the only task is staging them
  once for the offline guests.

## 8. Acceptance

Full three-way parity: HA + DR + distributed/DMZ on RHEL, with both RDQM
limitations demonstrably lifted, proven on a one-pass cold RHEL 9.6 rebuild and
captured in the parity run-report corpus as a `pcmk-rhel` column alongside the
existing two arms.
