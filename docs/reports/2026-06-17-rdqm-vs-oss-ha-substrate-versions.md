# RDQM-bundled vs. OSS-built HA substrate — version comparison

> **Issue:** #253
> **Date:** 2026-06-17
> **Method:** live read-only queries against the running lab arms
> (`rpm -q` on RDQM nodes, `dpkg-query` on the Ubuntu arm) + upstream release
> data. The live numbers are ground truth from the actual systems, not docs.

## TL;DR

RDQM does **not** ship Red Hat's HA Add-On — it ships a **LINBIT-built stack**
(the `.linbit` suffix on the RPMs; LINBIT is the company behind DRBD). The
result is **mixed, and counter-intuitive**:

- **DRBD layer — RDQM is the *most* current of anything we run.** `drbd-utils
  9.33.0` and a kernel-matched out-of-tree `kmod-drbd 9.2.16`. Unsurprising:
  LINBIT authors DRBD and builds RDQM's substrate.
- **Pacemaker — RDQM is the *oldest* of anything we run.** `pacemaker 2.1.2`
  (≈ late 2021), behind upstream's `2.1.9` (Oct 2024, the final 2.1.x before
  3.0) **and behind even the free Ubuntu 24.04 distro's `2.1.6`.**
- **Corosync — RDQM is current.** `3.1.9`, slightly ahead of Ubuntu's `3.1.7`.

So the intuition "IBM bundles old open-source" is **half right**: true for
Pacemaker, false for DRBD/Corosync. The support-boundary argument should target
**Pacemaker currency specifically**, not the stack as a whole.

## The data (live)

### RDQM arm — `rdqm-a1/a2/a3` and `rdqm-b1` (identical across all)

RHEL 9.6, kernel `5.14.0-570.12.1.el9_6`, MQ 9.4.5 Advanced.

| Package | Installed version | Source |
|---|---|---|
| `kmod-drbd` | **9.2.16**_5.14.0_570.12.1-1 | LINBIT, kernel-matched out-of-tree |
| `drbd-utils` | **9.33.0**-1.el9 | LINBIT |
| `pacemaker` | **2.1.2.linbit-4**.el9 | LINBIT build |
| `corosync` | **3.1.9.linbit-1.0.18**.643e01df.el9 | LINBIT build |
| `pcs` | *not installed* | RDQM uses `rdqmadm`, not `pcs` |

*(Queried 2026-06-17 via `ansible rdqm_a -m shell -a "rpm -q …"`. All three A
nodes and the B nodes returned identical versions.)*

### Pacemaker/SAN arm — `pcmk-a1/a2/a3` (`pcmk-ubuntu`)

Ubuntu 24.04 LTS, kernel `6.8.0-117-generic`. Distro packages via `apt`.

| Package | Installed version | Source |
|---|---|---|
| `pacemaker` | **2.1.6**-5ubuntu2 | Ubuntu 24.04 archive |
| `corosync` | **3.1.7**-1ubuntu3.2 | Ubuntu 24.04 archive |
| `pcs` | (installed) | Ubuntu 24.04 archive |
| `drbd-utils` | *not present in the live HA-only setup* | see note |
| DRBD module | in-tree, from `linux-modules-extra` (kernel 6.8) | Ubuntu kernel |

**DRBD-on-Ubuntu note.** The cluster nodes don't carry DRBD: the SAN arm uses
**iSCSI** for in-site HA, and **DRBD only appears in the cross-site DR setup**
(`drbd-san` role on the SAN target VMs, async protocol-A over `net-wan`). That
setup wasn't running at query time, so the Ubuntu DRBD versions aren't ground
truth yet. By install method they are: **`drbd-utils` from `apt`** (Ubuntu 24.04
ships `9.27.0`) plus the **in-tree DRBD module** in the 6.8 kernel's
`linux-modules-extra` (no DKMS / no out-of-tree build). *Confirm live with
`modinfo drbd` + `dpkg-query -W drbd-utils` when the DR setup is up.*

## Comparison + deltas

| Component | RDQM (LINBIT) | pcmk-ubuntu (distro) | Upstream latest | Read |
|---|---|---|---|---|
| **Pacemaker** | **2.1.2** (≈ Nov 2021) | 2.1.6 (2024) | **2.1.9** (Oct 2024); 3.0.0 (Jan 2025) | RDQM **oldest** — ~7 minor releases / ~3 yrs behind upstream, and behind free Ubuntu |
| **Corosync** | **3.1.9** | 3.1.7 | 3.1.9 | RDQM **current**, ahead of Ubuntu |
| **drbd-utils** | **9.33.0** (Nov 2025) | 9.27.0 (noble) | 9.34.0 (Mar 2026) | RDQM **near-latest**, well ahead of Ubuntu |
| **DRBD kmod** | **9.2.16** out-of-tree, kernel-matched | in-tree (kernel 6.8), ~9.1.x | 9.2.x (9.3.1 emerging) | RDQM **current 9.2.x**, proper out-of-tree; Ubuntu rides older in-tree |
| **Cluster CLI** | `rdqmadm` (pcs hidden) | `pcs` | — | RDQM wraps/abstracts the cluster |

## Why this matters for the comparison (#246, #238)

- **The support-boundary thesis should name Pacemaker.** RDQM's headline
  staleness is Pacemaker 2.1.2 — bug-fix/CVE currency and behaviour differences
  in the fault suite most plausibly trace there, not to DRBD.
- **It complicates "latest-and-greatest OSS beats bundled IBM."** On the DRBD
  layer the bundled IBM/LINBIT packages are *newer* than what the free distro
  gives our OSS arm. The clean win for self-built OSS is Pacemaker currency; the
  clean win for RDQM is DRBD currency.
- **It reframes the Native HA pitch (#246).** Native HA removes both Pacemaker
  *and* DRBD from the picture (raft in the QM). So the "which Pacemaker / which
  DRBD" currency question disappears entirely under Native HA — a point worth
  making explicitly alongside the in-QM-replication argument.

## Companion finding: RDQM's single floating IP is an `rdqmint` limit, not a Pacemaker version gap

A natural hypothesis, given the Pacemaker-version gap above: *RDQM is stuck with
one VIP because it bundles the old Pacemaker 2.1.2, and multi-VIP was added in a
later release (the Ubuntu arm runs 2.1.6 and does two).* The evidence says **no** —
the limit is architectural in IBM's `rdqmint` tooling, fully independent of the
Pacemaker version.

- **It's an MQ product limit with an MQ error code.** Our #216 verb spike
  (`docs/reports/2026-06-16-rdqm-verb-spike.md`) tried a second `rdqmint` (a
  partner-facing VIP, mirroring the Pacemaker arm). RDQM rejected it:
  `AMQ3877E: Floating IP address already exists for queue manager 'QMRDQM'.`
  `AMQ…` is an IBM MQ message — `rdqmint` refuses the second IP itself, before
  Pacemaker is involved.
- **IBM's `rdqmint` model is one floating IP per RDQM, by design.** The command is
  `rdqmint -m qmname -a -f <ipv4> -l <iface>` (one `-f`) / `-m qmname -d` — there
  is no facility for a second. (The doc's "must be unique" note is about not
  *sharing* an IP across RDQMs, a separate point.)
- **"Multiple VIPs" was never a Pacemaker feature to add.** Two VIPs = two
  ordinary `IPaddr2` resources in the group, which our Ubuntu arm does
  (`mq_vip` + `mq_vip_ext` in `mq-pcmk-qmgr`). Multiple resource instances are
  core Pacemaker behaviour predating 2.1.2, and `IPaddr2` is an OCF agent in the
  separate `resource-agents` package — not in Pacemaker at all. Running two VIPs
  on 2.1.6 works identically on 2.1.2. So there is no 2.1.2→2.1.6 changelog entry
  that "unlocked" a second VIP; that line of inquiry is the wrong layer.
- **Unchanged in MQ 10.0 (forward check, docs only).** The v10 *Creating and
  deleting a floating IP address* topic and the v10 `rdqmint` reference are
  byte-for-byte the same single-`-f` model as 9.4 — no multi-VIP support added.
  (Verified 2026-06-17 against `SSYHRD_10.0.0` — note v10's product code changed
  from `SSFKSJ`.)

**Conclusion:** RDQM's single-floating-IP constraint is a design property of
`rdqmint` (enforced by `AMQ3877E`), not a consequence of the bundled Pacemaker
2.1.2, and it is **unchanged through MQ 10.0**. The 2.1.2-vs-2.1.6 version delta
is real but coincidental to this limitation. This is the kind of constraint
**Native HA sidesteps**: it uses a single listener/connectivity address model and
isn't bound to `rdqmint`'s per-QM-IP rule.

## Open follow-ups

- Capture the **Ubuntu DRBD** versions live once the `pcmk_san_dr` setup is up
  (`modinfo drbd`, `dpkg-query -W drbd-utils`).
- If the `pcmk-rhel` arm (#238) is revisited, capture its **HA Add-On**
  Pacemaker/Corosync and **ELRepo** DRBD versions — a third data point (Red Hat
  Add-On vs LINBIT vs distro).
- Re-check after any MQ fix-pack: RDQM's bundled versions move with the MQ
  maintenance level, and the `kmod-drbd` must re-match on any RHEL kernel bump.

## Sources

**Live systems (ground truth, 2026-06-17):** `rpm -q` on `rdqm_a`/`rdqm_b`;
`dpkg-query -W` on `pcmk_a`; via `ansible … -m shell` over `build/inventory.ini`.

**Catalog / upstream:**
- IBM MQ RDQM Kernel Modules (catalog of shipped `kmod-drbd` builds per kernel) —
  <https://www.ibm.com/support/pages/ibm-mq-replicated-data-queue-manager-kernel-modules>
- Requirements for RDQM HA solution —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-requirements-rdqm-ha-solution>
- ClusterLabs Pacemaker releases (2.1.9 final 2.1.x, 2024-10; 3.0.0 2025-01) —
  <https://github.com/ClusterLabs/pacemaker/releases>
- LINBIT drbd-utils releases (9.33.0 2025-11; 9.34.0 2026-03) —
  <https://github.com/LINBIT/drbd-utils/releases>

**VIP finding:**
- RDQM floating IP (9.4) — `rdqmint`, one per RDQM —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-deleting-floating-ip-address>
- RDQM floating IP (10.0) — identical model, no multi-VIP —
  <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=availability-creating-deleting-floating-ip-address>
- `AMQ3877E` observed live in `docs/reports/2026-06-16-rdqm-verb-spike.md` (#216).
