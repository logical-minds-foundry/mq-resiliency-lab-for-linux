# RDQM vs. pacemaker-drbd — deviation ledger

This ledger is the source of truth for what IBM RDQM adds on top of the open-source
parts. It has one row per RDQM element, and every row cites the blueprint file it comes
from, under `docs/reference/rdqm-blueprint/`. See the
[blueprint README](rdqm-blueprint/README.md) for the full reading.

Status values:

- **exact**: the same upstream component with the same configuration.
- **partial**: the same behaviour through a different mechanism or different values.
- **none**: no open-source equivalent is built.
- **pending**: the open-source side is not built yet. Task 9 of epic
  logical-minds-foundry/.github#310 replaces every `pending`.

Row IDs are stable: `ST` storage, `RP` replication, `CL` cluster, `DR` disaster
recovery, `TL` lifecycle tooling, `SC` replication security, `SP` support and
installation. Blueprint paths below are relative to `docs/reference/rdqm-blueprint/`.

Seeded by #1425 from the 2026-10-16 capture. The pacemaker-drbd column states the
**plan's** intended equivalent. Where the blueprint contradicts that plan on a
structural point, the row says so, and the affected task (#1427 or #1428) is revised
before it starts.

## Storage

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| ST-1 | `drbdpool` VG on a local disk (`/dev/vdb`, `<10.00g`) | `rdqm-a1/vgs.txt`, `rdqm-a1/lsblk.txt` | `drbdpool` VG on the extra disk (`drbd9` role) | exact | — |
| ST-2 | Per-QM data LV `drbdpool/rdqmapp_00`, 3 GiB (`rdqmapp-size=3072`) | `rdqm-a1/lvs.txt`, `rdqm-a1/pcmk-config.txt` | Per-QM LV in `drbdpool`, 3G (`pdrbd-qm`). The plan names it `<qm>`; RDQM uses `<qm>_00` | pending | Naming only |
| ST-3 | `ext4` on the DRBD device, mounted at `/var/mqm/vols/<qm>` with default options (`rw,relatime`; `seclabel` is SELinux) | `rdqm-a1/mounts.txt` | `ext4` with `defaults` at the same path | pending | — |
| ST-4 | The filesystem sits on the **stacked DR device** (`/dev/drbd/by-res/rdqmapp.dr/0` = `drbd7001`), not on the HA device | `rdqm-a1/lsblk.txt`, `rdqm-a1/pcmk-config.txt` | The plan mounts `/dev/drbd<minor>` of a single resource | pending | **Structural.** It follows from RP-1 |
| ST-5 | QM data and logs under the replicated mount (`DataPath=/var/mqm/vols/rdqmapp/qmgr/rdqmapp`); every node carries the same `addmqinf` definition, site B included | `rdqm-a1/mqs-ini.txt`, `rdqm-*/dspmqinf.txt` | `crtmqm -md/-ld` on the mount, plus `addmqinf` on the other nodes | pending | — |

## Replication

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| RP-1 | **Two DRBD resources per HA/DR QM.** `rdqmapp` is the HA resource: 3 nodes of one site, `connection-mesh`. `rdqmapp.dr` is the DR resource, `stacked-on-top-of rdqmapp`, with 2 members: this site's HA primary and the other site as one logical peer, `encrypted.remote` | `rdqm-a1/drbd-dump.txt`, `rdqm-b1/drbd-dump.txt` | The plan has **one six-peer resource** with per-connection protocols | pending | **Structural contradiction**: Task 5's template and Task 6's DR wiring must change |
| RP-2 | HA quorum is site-local with a fixed count: `quorum 2` of 3, `on-no-quorum io-error`, `auto-promote no` | `rdqm-a1/drbd-dump.txt` | The plan has `quorum majority` over 6 peers and `on-no-quorum suspend-io` | pending | With one six-peer resource, a WAN cut leaves each site 3 of 6 peers and freezes the live site. RDQM avoids this because the WAN peer is never an HA quorum member |
| RP-3 | DR quorum: `rdqmapp.dr` has `quorum majority` and `on-no-quorum io-error` over its 2 members | `rdqm-a1/drbd-dump.txt` | Not in the plan | pending | The capture does not show how the live primary behaves on a WAN cut. Drill 3 settles it on both arms |
| RP-4 | HA replication uses protocol C (DRBD default) on port 7000, minor 100, over `net-hb-<site>` | `rdqm-a1/drbd-dump.txt`, `rdqm-a1/rdqm-ini.txt` | Protocol C over the hb network, but the plan uses port 7001 and minor 100 | pending | Port value only |
| RP-5 | DR replication uses protocol A on port 7001, minor 7001, over `net-wan`, with three `path`s (one per remote node) to the single logical peer | `rdqm-a1/drbd-dump.txt`, `rdqm-a1/pcmk-config.txt` (`rdqmapp-dr-*`) | The plan has protocol A over `net-wan` as 9 node-to-node cross-site connections | pending | Structural, via RP-1 |
| RP-6 | Common options: `twopc-timeout 100`, `ping-timeout 40`, `socket-check-timeout 5`, `max-buffers 40k`, `c-fill-target 1M`, `c-max-rate 500M`, `c-min-rate 100M`, `resync-without-replication no` | `rdqm-a1/drbd-dump.txt`, `rdqm-a1/drbd-conf.txt` | Plan Task 6 proposes the `drbd-operations.md` buffers "unless the blueprint shows RDQM's own values". It does, so use these | pending | — |
| RP-7 | No `after-sb-*` policy (DRBD default: `disconnect`) and no DRBD `fencing` (default `dont-care`); `rr-conflict retry-connect`, `verify-alg sha1`, `disable-write-same yes` on HA; `allow-remote-read no` on DR | `rdqm-a1/drbd-dump.txt` | The plan sets `after-sb-0pri discard-zero-changes`, `after-sb-1pri discard-secondary` and `after-sb-2pri disconnect` | pending | The plan's automatic split-brain recovery is **not** RDQM behaviour |
| RP-8 | LINBIT DRBD 9.3.3 kernel module (`+ptf` build) with drbd-utils 9.33.1 | `rdqm-a1/drbd-version.txt`, `rdqm-a1/packages.txt` | LINBIT DRBD 9 (`drbd-dkms`) from LINBIT's PPA | pending | Versions may differ; record them in Task 9 |

## Cluster

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| CL-1 | One three-node corosync/Pacemaker cluster per site, using votequorum (expected 3, quorum 2), knet with `token 10000` and no corosync crypto, ring 0 on `net-hb-<site>`. No cluster spans the sites | `rdqm-*/corosync-conf.txt`, `rdqm-*/quorum.txt` | One `pcmk-cluster` per site (`mqpdrbd-a` / `mqpdrbd-b`) | pending | — |
| CL-2 | `stonith-enabled=false` and no fencing devices; `no-quorum-policy` not set (default `stop`) | `rdqm-a1/pcmk-config.txt` | The plan sets `stonith-enabled=false` and `no-quorum-policy=stop` | pending | Matches the hypothesis. RDQM uses no fencing |
| CL-3 | `resource-stickiness=1` (resource default) | `rdqm-a1/pcmk-config.txt` | The plan uses `resource-stickiness=1000` | pending | Value deviation; reconcile to 1 |
| CL-4 | HA DRBD role: `ocf:linbit:drbd` promotable clone `ms_drbd_<qm>` with `adjust_master_score="0 10 10000 10000"` | `rdqm-a1/pcmk-config.txt` | `ocf:linbit:drbd` promotable clone `drbd_qm-clone` | pending | Same upstream agent |
| CL-5 | DR DRBD role: a second `ocf:linbit:drbd` promotable clone `ms_drbd_dr_<qm>` with `clone-max=1` and `wfc_timeout=false`, colocated with the HA Master and ordered after HA promote | `rdqm-a1/pcmk-config.txt` | None in the plan | pending | Structural, via RP-1. The agent is upstream, not IBM |
| CL-6 | QM resource: **`ocf:ibm:rdqm`** (IBM-proprietary; start timeout 43200 s, monitor 30 s) | `rdqm-a1/pcmk-config.txt`, `rdqm-a1/ocf-agents.txt` | `systemd:mq-<QM>` unit in `mq_group` | pending | IBM agent behaviour (monitor depth, start semantics) is not reproduced |
| CL-7 | **`ocf:ibm:rdqmx`** (IBM-proprietary, undocumented) ordered between the filesystem and the floating IP | `rdqm-a1/pcmk-config.txt` | None | pending | What it does is unknown; empty meta-data (supplementary probe) |
| CL-8 | Filesystem agent `ocf:heartbeat:Filesystem` with `force_unmount=safe` and `fast_stop=no` | `rdqm-a1/pcmk-config.txt` | `ocf:heartbeat:Filesystem` with `OCF_CHECK_LEVEL=20` monitor | pending | Parameter values differ |
| CL-9 | Floating IP `ocf:heartbeat:IPaddr2` (`cidr_netmask=24`, `nic=enp6s0`) on `net-data-<site>` | `rdqm-a1/pcmk-config.txt`, `rdqm-a1/rdqmstatus-qm.txt` | `ocf:heartbeat:IPaddr2` in `mq_group` | pending | — |
| CL-10 | Constraint chain: HA promote → DR start; DR promote → fs → rdqmx → ip → QM (each `inf` colocated); `location <qm> 50:` on the preferred node | `rdqm-a1/pcmk-config.txt` | The plan uses a group `mq_fs → mq_vip → mq_qm` colocated with the HA Promoted role | pending | IP-before-QM order matches; the fs → IP order is reversed relative to the plan's group; no preferred-location score in the plan |
| CL-11 | RDQM keeps its own state in the CIB: property set `rdqm:` (`<qm>-dr-role`, `-qm-type`, `-ha-state`, ports, IP lists, encryption flags) and node attributes (`drbd-replication`, `mqm-uid/gid`, `mqvrmf`) | `rdqm-a1/pcmk-config.txt` | None | none | Tooling reads this state; the open-source arm reads live state instead |
| CL-12 | Pacemaker alert `rdqm-alert` → `/var/mqm/rdqm/rdqmalert` | `rdqm-a1/pcmk-config.txt` | None | pending | — |

## Disaster recovery

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| DR-1 | Site B as DR secondary: the full resource set is defined; the DR clone is running Unpromoted (`target-role=Unpromoted`); the **HA clone is Promoted on `rdqm-b1`**; the fs, rdqmx, IP and QM are Stopped | `rdqm-b1/pcmk-config.txt`, `rdqm-b1/pcmk-status.txt`, `rdqm-b1/drbd-status.txt` | The plan creates site B's DRBD clone `--disabled` (target-role Stopped) | pending | Structural, via RP-1: site B's HA layer must run promoted under the stacked DR secondary |
| DR-2 | Operator-driven switch (`rdqmdr -s` / `-p`). Judgment: it flips the DR clone `target-role` and the `<qm>-dr-role` property | `rdqm-a1/pcmk-config.txt`, `rdqm-b1/pcmk-config.txt` | `site-pdrbd-dr-switch.yml` (disable / enable resources) | pending | Mechanism to be revised for RP-1 |
| DR-3 | **Revert-to-snapshot LV** `drbdpool/<qm>_00-before-resync` (`<3.02g`, linear, active, not open) on all six nodes, maintained by IBM's `before-resync-*` / `after-resync-target` DRBD handlers on the DR resource (`rdqmhandler`). Its last refresh time is in the CIB (`rdqmapp-dr-before-resync-time`, set during the ordinary initial DR sync) | `rdqm-a1/lvs.txt`, `rdqm-a1/drbd-dump.txt`, `rdqm-a1/pcmk-config.txt`, `rdqm-b1/pcmk-config.txt` | Not built | **none** | The recovery site cannot be reverted to its pre-resync state after a failed or partial resync. The open-source arm configures no `before-resync-*` handlers, so its DR resync does not depend on this LV |

## Lifecycle tooling

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| TL-1 | Cluster CLI is `crm` (crmsh 4.6.2); **`pcs` is not installed** | `rdqm-a1/packages.txt`; supplementary probe | `pcs` on Ubuntu | partial | Commands differ (`crm configure show` vs `pcs config`); not a behaviour difference |
| TL-2 | `rdqm.ini` (per-site node list and `HA_Replication` addresses), `rdqmadm`, `crtmqm -sx/-rr`, `rdqmdr`, `rdqmstatus` | `rdqm-a1/rdqm-ini.txt`, `rdqm-a1/rdqmstatus-*.txt` | Ansible roles and plays plus `mqlab` verbs | pending | — |
| TL-3 | DRBD handlers (`quorum-lost`, `disconnected`, `after-resync-target`, `before-resync-*`), all `/var/mqm/rdqm/<h>` → `/opt/mqm/bin/rdqmhandler` | `rdqm-a1/drbd-dump.txt`; supplementary probe | None | pending | IBM reactions to quorum loss and disconnects are not reproduced |
| TL-4 | Units: `rdqm.service` and `pacemaker.service` enabled; `corosync.service` and `drbd.service` disabled | `rdqm-a1/units.txt` | `pacemaker` / `corosync` enabled by `pcmk-cluster` | pending | — |
| TL-5 | RDQM replaces the stock `global_common.conf` (saved as `.rdqmsave`) | `rdqm-a1/drbd-conf.txt` | The blueprint values from RP-6 are rendered by `pdrbd-qm` | pending | — |

## Replication security

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| SC-1 | HA and DR replication over DRBD kernel TLS (`tls yes`, `tlshd`), with per-node certificates from a private "RDQM Replication CA" (SAN includes `encrypted.remote`). Enabled in this lab by `crtmqm -re` (#545) | `rdqm-a1/drbd-dump.txt`, `rdqm-a1/drbd-conf.txt` (PEM bodies redacted), `rdqm-a1/rdqmstatus-qm.txt` | None in the plan (plain TCP) | pending | Replication traffic is unencrypted unless the open-source arm adds `tls yes` + `tlshd` |
| SC-2 | No corosync crypto (`crypto_cipher none`, `crypto_hash none`) | `rdqm-a1/corosync-conf.txt` | `pcmk-cluster` default | pending | — |

## Support and installation

| ID | RDQM element | Blueprint source | pacemaker-drbd equivalent | Status | Consequence |
|---|---|---|---|---|---|
| SP-1 | Cluster stack shipped by IBM as LINBIT builds: pacemaker 3.0.1, corosync 3.1.9, resource-agents 4.17.0, drbd-pacemaker 9.33.1, crmsh 4.6.2 | `rdqm-a1/packages.txt` | Ubuntu archive pacemaker / corosync / pcs plus LINBIT PPA DRBD | pending | Version skew; support comes from the community, not IBM or LINBIT |
| SP-2 | DRBD kmod built for kernel `6.12.0-211.7.1` running on `6.12.0-211.7.3` (kABI-compatible) | `rdqm-a1/rdqmstatus-group.txt` | DKMS build per box kernel (`drbd9` role) | pending | — |
| SP-3 | RHEL 10.2 with SELinux (`seclabel`) | `rdqm-a1/lsblk.txt`, `rdqm-a1/mounts.txt` | Ubuntu (AppArmor) | partial | Platform difference by design |
