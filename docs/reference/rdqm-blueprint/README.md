# RDQM blueprint (captured live configuration)

This directory holds the configuration that IBM RDQM **generates** on a live HA/DR
group. It is the blueprint the `pcmk-drbd-ubuntu` arm (epic
logical-minds-foundry/.github#310) is built to. The deviation ledger
[`../rdqm-vs-vanilla-deviations.md`](../rdqm-vs-vanilla-deviations.md) cites it row by row.

Each `<host>/<item>.txt` file is unedited output from the read-only capture playbook
`ansible/capture-rdqm-blueprint.yml`, with two redactions. Every fact below cites the
file it comes from. Where a fact comes from a later read-only probe of the same live
group rather than from the capture, it is marked **(supplementary probe)**.

Each claim is labelled as one of two kinds:

- **Data** is what a file shows.
- **Judgment** is reasoning on top of the data. Judgments are marked as such.

## 0. Provenance and redaction

- **Capture:** run on 2026-10-16 at 21:45:55Z against a cold-built, healthy `rdqm-rhel` on
  cloud x86_64. HA and DR were both formed, and `RDQMAPP` was running on `rdqm-a1`.
  The playbook was `ansible/capture-rdqm-blueprint.yml` as merged by #1600 (PR #1601),
  which keeps both stdout and stderr; the capture record is on #1425. The result was 6 hosts × 20 items with `failed=0`.
- **Redaction 1 (at capture time):** DRBD `shared-secret` values are replaced with
  `"REDACTED"`. The only `shared-secret` text present is a commented example in
  `global_common.conf.rdqmsave`, because RDQM uses TLS rather than a shared secret
  (§3).
- **Redaction 2 (at commit time, #1425):** `drbd-conf.txt` dumps every file in
  `/etc/drbd.d/`. On this arm that directory contains each node's DRBD TLS **private
  key** (`rdqm-<host>.key`, mode 0600) and two certificates (`rdqm-<host>.crt`,
  `rdqm-repl-ca.crt`). The lab's `rdqm-replication-tls` role (#545) provisions all
  three. Every PEM body is replaced with a
  `[PEM … body REDACTED for the committed blueprint (#1425)]` marker. The file names
  and `ls -l` lines are kept as data. No keystore (`*.kdb`, `*.p12`, `*.sth`, `*.jks`)
  and no corosync `authkey` appears anywhere.
- **Key handling from #1603 on:** this capture predates the fix, so its private keys
  were removed by hand at commit time (Redaction 2). The playbook now records a key file
  in `/etc/drbd.d/` by path only, whether it is matched by name or by a `PRIVATE KEY`
  block in its content. It replaces any private key block left in the DRBD items with a
  marker. It also fails the run if `PRIVATE KEY` appears in any capture item before the
  write, or in any written file afterwards.
- **MQ level:** 10.0.1.0. Sources: `lab/mq-version`; `MQSeries*-10.0.1-0` in
  `rdqm-a1/packages.txt`; node attribute `mqvrmf=10.0.1.0` in `rdqm-a1/pcmk-config.txt`.
- **OS:** RHEL 10.2 (install media label `RHEL-10-2-BaseOS-x86_64` in
  `rdqm-a1/lsblk.txt`). The running kernel is `6.12.0-211.7.3`, and the DRBD module was
  built for `6.12.0-211.7.1` (`rdqm-a1/rdqmstatus-group.txt`).
- **DRBD:** kernel module 9.3.3 (`9.3.3+ptf.28.g6c10a1e64888`) and drbd-utils 9.33.1,
  according to `rdqm-a1/drbd-version.txt`. Package `kmod-drbd-9.3.3+ptf.28…` is in
  `rdqm-a1/packages.txt`. **The module is LINBIT DRBD 9.** The `+ptf` suffix is a
  LINBIT program-temporary-fix build, and every cluster package carries a `.linbit`
  release tag. This settles spec D2: RDQM ships DRBD 9, not the 8.4 in-kernel module.
- **Cluster stack:** every package is a LINBIT build (`rdqm-a1/packages.txt`):
  - `pacemaker-3.0.1.linbit-1.el10`
  - `corosync-3.1.9.linbit-2.0.el10`
  - `resource-agents-4.17.0.linbit-3.0.36`
  - `drbd-pacemaker-9.33.1` (supplies `ocf:linbit:drbd`)
  - `crmsh-4.6.2.linbit`
- **Cluster CLI:** `crm` (crmsh). **`pcs` is not installed on the RDQM nodes**
  (supplementary probe: `command -v pcs` finds nothing; `/sbin/crm` is present). The
  capture's `pcmk-config.txt` is therefore `crm configure show` output, which uses crm
  syntax and legacy `Master`/`Slave` role names, not `pcs config`.
- **Identical across all six hosts:** `packages.txt`, `ocf-agents.txt`, `mqs-ini.txt`,
  `units.txt` and `drbd-version.txt`.

## 1. Storage

- **VG:** `drbdpool` on the extra disk `/dev/vdb`, size `<10.00g`, holding 2 LVs, with
  `3.98g` free (`rdqm-a1/vgs.txt`, `rdqm-a1/lsblk.txt`). It is the same on every node.
- **QM data LV:** `drbdpool/rdqmapp_00`, `3.00g`, linear, open
  (`rdqm-a1/lvs.txt`). The size is the `crtmqm` default, recorded in the CIB as
  `rdqmapp-size=3072` (MB) (`rdqm-a1/pcmk-config.txt`). The LV name carries an `_00`
  volume suffix: it is `rdqmapp_00`, not `rdqmapp`.
- **DR snapshot LV:** `drbdpool/rdqmapp_00-before-resync`, `<3.02g`, attributes
  `-wi-a-----` (active, **not open**), with no `Origin`
  (`rdqm-a1/lvs.txt`; the same on all six nodes). A supplementary probe shows segment
  type `linear`. It is therefore a plain LV slightly larger than the data LV, not an
  LVM snapshot of it. It belongs to no DRBD resource and to no Pacemaker resource
  (`rdqm-a1/drbd-dump.txt`, `rdqm-a1/pcmk-config.txt`). For what drives it, see §5.
  - **Data (IBM):** "Each queue manager requires two logical volumes to support the
    reverting to snapshot operation, so each DR RDQM is allocated just over twice the
    storage". Source:
    <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=recovery-requirements-rdqm-dr-solution>
    (cached as `refs/ibm-docs/ibm-mq/10.0.x/recovery-requirements-rdqm-dr-solution/content.txt`).
  - **Judgment:** the size (data LV plus about 20 MB) fits a full copy of the
    backing device, including DRBD's internal metadata.
- **Device stack:** `vdb → drbdpool-rdqmapp_00 → drbd100 → drbd7001`
  (`rdqm-a1/lsblk.txt`). The filesystem sits on the **stacked DR device**
  `/dev/drbd7001`, not on the HA device `drbd100`.
- **Filesystem:** `ext4`, mounted at `/var/mqm/vols/rdqmapp` with
  `rw,relatime,seclabel` (`seclabel` is SELinux, so these are the defaults). It is
  mounted only on the active node (`rdqm-a1/mounts.txt`; `rdqm-a2/mounts.txt` and
  `rdqm-b1/mounts.txt` show nothing mounted).
- **QM paths:** `DataPath=/var/mqm/vols/rdqmapp/qmgr/rdqmapp`, `Prefix=/var/mqm`,
  `Directory=rdqmapp`, `HA=Replicated`, `DR=Replicated` (`rdqm-a1/mqs-ini.txt`,
  `rdqm-a1/dspmqinf.txt`). Every node carries the same `addmqinf` definition,
  including the site-B nodes (`rdqm-b1/dspmqinf.txt`).

## 2. Replication

### 2.1 Two DRBD resources per QM: HA, with DR stacked on top

**Data.** RDQM defines **two separate DRBD resources** for one HA/DR queue manager
(`rdqm-a1/drbd-dump.txt`, `rdqm-a1/drbd-conf.txt`):

| | HA resource `rdqmapp` | DR resource `rdqmapp.dr` |
|---|---|---|
| Members | the 3 nodes of **one site**, `connection-mesh` | 2: `stacked-on-top-of rdqmapp` (this site) and `encrypted.remote` (the other site) |
| Backing disk | `/dev/drbdpool/rdqmapp_00`, `meta-disk internal` | `/dev/drbd100`, the HA device (supplementary probe: `drbdsetup show rdqmapp.dr`) |
| Device minor | 100 (`rdqmapp-drbd-minor=100`) | 7001 |
| Port | 7000 (`rdqmapp-drbd-port=7000`) | 7001 (`rdqmapp-dr-port=7001`) |
| Network | `net-hb-<site>` (172.16.1.3x / 172.16.2.4x) | `net-wan` (10.99.0.3x ↔ 10.99.0.4x) |
| Protocol | C (DRBD default; not set) | **A** (async) |
| `quorum` | **`2`** (a fixed count of 3) | `majority` |
| `on-no-quorum` | `io-error` | `io-error` |
| `auto-promote` | `no` | `no` |
| net | `verify-alg sha1`, `rr-conflict retry-connect`, `tls yes` | `verify-alg sha1`, `allow-remote-read no`, `tls yes` |
| disk | `disable-write-same yes` | (common only) |
| handlers | `quorum-lost`, `after-resync-target`, `disconnected` | `before-resync-source`, `before-resync-target`, `after-resync-target`, `disconnected` |

- **The remote peer.** The DR resource's remote peer is **one logical node**,
  `encrypted.remote` (node-id 1 on site A, 0 on site B), with `disk _remote`.
  It is reached by **three `path`s**, one to each remote-site WAN address
  (`rdqm-a1/drbd-dump.txt`, `rdqm-b1/drbd-dump.txt`). Whichever remote node is that
  site's HA primary answers. `rdqmstatus` reports
  `DR current remote IP address: 10.99.0.41` (`rdqm-a1/rdqmstatus-qm.txt`).
  - **Judgment:** `encrypted.remote` is also the TLS certificate SAN, as the
    `rdqm-qm-create.sh` comment on the default group DNS name says.
- **Stacking.** The `stacked-on-top-of rdqmapp` block lists every site node
  (`# on rdqm-a1 rdqm-a2 rdqm-a3`), so the DR resource runs on whichever node is the
  HA primary.
- **Common section.** Identical on all six nodes (`rdqm-a1/drbd-dump.txt`):
  - options: `twopc-timeout 100`
  - net: `ping-timeout 40`, `socket-check-timeout 5`, `max-buffers 40k`
  - disk: `c-fill-target 1M`, `c-max-rate 500M`, `c-min-rate 100M`,
    `resync-without-replication no`

  RDQM saved the stock file as `global_common.conf.rdqmsave` and replaced it
  (`rdqm-a1/drbd-conf.txt`).
- **Not set, so DRBD defaults apply (Data):** `after-sb-0pri`, `after-sb-1pri`,
  `after-sb-2pri` and `fencing`. Neither resource nor the common section sets them
  (`rdqm-a1/drbd-dump.txt`), which means DRBD's `disconnect` / `dont-care`
  behaviour. There is no `fence-peer` handler.
- **Replication security.** Both resources use kernel TLS (`tls yes`). Each node has a
  certificate signed by a private "RDQM Replication CA" (`rdqm-a1/drbd-conf.txt`, PEM
  bodies redacted). `rdqmstatus` reports `HA TLS encryption: Yes` and
  `DR TLS encryption: Yes` (`rdqm-a1/rdqmstatus-qm.txt`). The CIB records
  `rdqmapp-encrypt-ha=1` and `rdqmapp-encrypt-dr=1`.
  - The lab turned this on with `crtmqm -re` (#545); it is not an RDQM default.
  - `tlshd` is active and enabled (supplementary probe). The capture's unit filter
    does not include it.
- **Live state (Data):**
  - **Site A:** `rdqmapp` is Primary on `rdqm-a1`, with both site peers UpToDate
    over TLS (`rdqm-a1/drbd-status.txt`).
  - **Site B:** `rdqmapp` is **Primary on `rdqm-b1`** (`rdqm-b1/drbd-status.txt`).
  - **The DR resource:** `drbdadm status all` does not list the stacked resource.
    `drbdsetup status --verbose` does (supplementary probe):
    - On `rdqm-a1`, `rdqmapp.dr` is Primary, `disk:UpToDate`, `quorum:yes`, and
      Connected to `encrypted.remote` (Secondary).
    - On `rdqm-b1`, `rdqmapp.dr` is Secondary, `disk:UpToDate`, `quorum:yes`, and
      Connected to a Primary peer.

### 2.2 How quorum is scoped across HA and DR peers

**Data.** RDQM does **not** form one six-peer resource. It scopes quorum per resource:

- **HA quorum is site-local and fixed.** `rdqmapp` has only the three nodes of one
  site, with `quorum 2` (`rdqm-a1/drbd-dump.txt`, `rdqm-b1/drbd-dump.txt`). The other
  site's nodes are never members, so a WAN cut cannot affect HA quorum.
- **Cluster quorum is site-local.** Each site is a separate three-node corosync /
  Pacemaker cluster with `corosync_votequorum`, expected votes 3 and quorum 2
  (`rdqm-a1/quorum.txt`, `rdqm-b1/quorum.txt`, `rdqm-*/corosync-conf.txt`). No
  cluster spans the sites.
- **DR quorum is a separate two-member resource.** `rdqmapp.dr` has `quorum majority`
  and `on-no-quorum io-error` between the two sites' current HA primaries.

**Open question (not settled by the capture):** how `rdqmapp.dr` keeps quorum when the
WAN is cut. Its two members each have one vote, so whether the live primary keeps
quorum after an ungraceful loss of its only peer depends on DRBD 9's two-node quorum
rules and on what IBM's `disconnected` handler (`/opt/mqm/bin/rdqmhandler`) does.
Evidence so far:

- **Supplementary probe (kernel log):** at attach, before ever connecting, each
  site's DR member reported `quorum( no -> yes )` on its own.
- **`docs/reference/lab-gotchas.md`:** a DR-link drop during the initial create made
  `rdqmapp.dr` lose quorum and fail I/O, but that disk was still Inconsistent.

The cut has not been observed on an UpToDate pair. Drill 3 (DR-link outage on both
arms, Task 8) is where this is settled. It is listed as a human-verify item.

**Observed later (#1666, 2026-10-19):** on an UpToDate pair, after an ungraceful WAN cut,
the live DR primary kept `quorum:yes` and its QM kept running. A new HA primary attached
the DR copy alone and promoted it. See
[`observations/README.md`](observations/README.md) §1.2 (S1, S3), which also records what
RDQM does on forced DR, on a returning old primary, and on HA quorum loss.

### 2.3 Networks (Data, matched against `lab/topology.yaml`)

| Traffic | Network | Addresses |
|---|---|---|
| HA replication (`rdqmapp`) | `net-hb-a` / `net-hb-b` | `rdqm-ini.txt` `HA_Replication=172.16.x.y`; node attribute `drbd-replication` |
| Corosync ring 0 | the same `net-hb-*` | `corosync-conf.txt` `ring0_addr` |
| DR replication (`rdqmapp.dr`) | `net-wan` | `rdqmapp-dr-local-ip` and `rdqmapp-dr-remote-ip` in `pcmk-config.txt` |
| Floating IP | `net-data-*` | `p_ip_rdqmapp` `ip=10.10.1.100` (site A) / `10.10.2.100` (site B), `nic=enp6s0` |

Corosync uses knet with `token: 10000`, `knet_ping_interval: 500`,
`knet_ping_timeout: 5000`, `knet_pong_count: 2`, `crypto_cipher: none` and
`crypto_hash: none` (`rdqm-a1/corosync-conf.txt`).

## 3. Cluster (Pacemaker)

Source for this section: `rdqm-a1/pcmk-config.txt` and `rdqm-a1/pcmk-status.txt`, with
site B from `rdqm-b1/…`.

- **Properties:**
  - `stonith-enabled=false`. There are **no fencing devices**.
  - `have-watchdog=false`.
  - `no-quorum-policy` is **not set**, so Pacemaker's default `stop` applies. A
    supplementary probe of `crm_attribute -n no-quorum-policy -G` found no such
    attribute.
  - Resource default: `resource-stickiness=1`.
- **RDQM state in the CIB:** a separate property set, `rdqm:`, holds RDQM's own state:
  - `rdqmapp-dr-role` is `1` on site A and `2` on site B (judgment: primary and
    secondary).
  - Also `rdqmapp-qm-type=3`, `rdqmapp-ha-state=0`, `rdqmapp-size`,
    `rdqmapp-drbd-port`, `rdqmapp-drbd-minor`, `rdqmapp-dr-port`, the DR local and
    remote IP lists, `rdqmapp-encrypt-ha` and `rdqmapp-encrypt-dr`.
  - `rdqmapp-dr-before-resync-time` (see §5).
- **Node attributes:** `drbd-replication`, `mqm-uid`, `mqm-gid`, `mqvrmf`,
  `rdqmapp-name` and `rdqmapp-dr-replication`.
- **Alert:** `alert rdqm-alert "/var/mqm/rdqm/rdqmalert"`.

### 3.1 Resources

| Resource | Agent | Role |
|---|---|---|
| `p_drbd_rdqmapp` / clone `ms_drbd_rdqmapp` | `ocf:linbit:drbd`, `drbd_resource=rdqmapp`, `adjust_master_score="0 10 10000 10000"` | HA DRBD; promotable clone (no `clone-max`, so 3) |
| `p_drbd_dr_rdqmapp` / clone `ms_drbd_dr_rdqmapp` | `ocf:linbit:drbd`, `drbd_resource=rdqmapp.dr`, `wfc_timeout=false` | DR DRBD; promotable clone, **`clone-max=1`** |
| `p_fs_rdqmapp` | `ocf:heartbeat:Filesystem`, `device=/dev/drbd/by-res/rdqmapp.dr/0`, `fstype=ext4`, `force_unmount=safe`, `fast_stop=no` | QM filesystem on the DR device |
| `p_rdqmx_rdqmapp` | **`ocf:ibm:rdqmx`** (IBM) | IBM-internal step between the filesystem and the IP. Its meta-data has an empty `shortdesc`/`longdesc` (supplementary probe) |
| `p_ip_rdqmapp` | `ocf:heartbeat:IPaddr2`, `cidr_netmask=24`, `nic=enp6s0` | floating IP |
| `rdqmapp` | **`ocf:ibm:rdqm`** (IBM) | the queue manager (start timeout 43200 s, monitor 30 s) |

**IBM-proprietary agents** (provider `ibm`, from `rdqm-a1/ocf-agents.txt`) are
`ocf:ibm:rdqm` and `ocf:ibm:rdqmx`. **Both DRBD roles use the upstream
`ocf:linbit:drbd`.**

Operation timeouts on both DRBD primitives:

- monitor 20 s (Slave) and 10 s (Master), each with a 20 s timeout
- start 240 s, stop 100 s
- promote 90 s, demote 90 s

### 3.2 Constraints (one chain, all `inf` / `Mandatory`)

```text
ms_drbd_rdqmapp:promote → ms_drbd_dr_rdqmapp:start    (DR clone colocated with the HA Master)
ms_drbd_dr_rdqmapp:start, :promote → p_fs_rdqmapp:start   (fs colocated with the DR Master)
p_fs_rdqmapp → p_rdqmx_rdqmapp → p_ip_rdqmapp → rdqmapp  (each colocated with the previous)
location rdqmapp_preferred_location rdqmapp 50: rdqm-a1   (site B: rdqm-b1)
```

## 4. DR wiring: site B while it is the DR secondary

Sources: `rdqm-b1/pcmk-config.txt`, `rdqm-b1/pcmk-status.txt`,
`rdqm-b1/rdqmstatus-group.txt` and `rdqm-b1/drbd-status.txt`.

- **Resources:** site B holds the **same resource set and constraints** as site A, in
  its own cluster. Its floating IP is `10.10.2.100` and its preferred location is
  `rdqm-b1`.
- **DR clone:** `ms_drbd_dr_rdqmapp` has `target-role=Unpromoted` on site B, against
  `Promoted` on site A. It is running Unpromoted on `rdqm-b1`, and `crm_mon` counts it
  as the one "DISABLED" instance. DRBD shows `rdqmapp.dr` Secondary, UpToDate and
  Connected (supplementary probe).
- **HA clone:** `ms_drbd_rdqmapp` is **running and Promoted on `rdqm-b1`**, with b2
  and b3 Unpromoted. The site's HA layer stays live underneath the stacked DR
  secondary.
- **Downstream resources:** `p_fs_rdqmapp`, `p_rdqmx_rdqmapp`, `p_ip_rdqmapp` and
  `rdqmapp` are all Stopped, because the DR clone is not promoted. `rdqmstatus` reports
  `DR role: Secondary`, `Queue manager status: Ended immediately` and
  `HA status: Normal`.
- **Judgment:** the operator-driven `rdqmdr -s` / `-p` switch flips the DR clone's
  `target-role` and the `rdqmapp-dr-role` property. The chain in §3.2 then starts the
  filesystem, IP and QM at the new primary site. The capture shows only the steady
  state; Task 8's drills observe the switch itself.

## 5. RDQM lifecycle surface

- **`rdqm.ini`:** the site's three nodes and their `HA_Replication` addresses, with no
  DR stanza (`rdqm-a1/rdqm-ini.txt`, `rdqm-b1/rdqm-ini.txt`).
- **Units** (`rdqm-a1/units.txt`):
  - `rdqm.service`, `pacemaker.service` and `mqweb.service` are enabled.
  - `corosync.service` and `drbd.service` are disabled; Pacemaker starts corosync.
  - The `lab-rdqm-state.timer` unit is the lab's own.
- **DRBD handlers:** every `/var/mqm/rdqm/<handler>` is a symlink to one IBM binary,
  `/opt/mqm/bin/rdqmhandler` (supplementary probe: `ls -la /var/mqm/rdqm/`).
- **The snapshot path in ordinary operation (Data):**
  - The DR resource wires `before-resync-target`, `before-resync-source` and
    `after-resync-target` to `rdqmhandler` (`rdqm-a1/drbd-dump.txt`).
  - Both sites' CIBs record `rdqmapp-dr-before-resync-time`, at `21:12:09.374Z` on
    site A and `21:12:09.525Z` on site B (`rdqm-a1/pcmk-config.txt`,
    `rdqm-b1/pcmk-config.txt`). That is one second after `rdqmapp.dr` first connected
    at 21:12:08 (supplementary probe, kernel log). The before-resync handler therefore
    ran during the **ordinary initial DR sync**, not during a revert.
  - The HA resource has no `before-resync-*` handler.
  - **Judgment:** RDQM refreshes the `-before-resync` LV around each DR resync so that
    "revert to snapshot" has something to revert to. A DR resync is part of normal
    operation after any DR-link outage, so RDQM's ordinary DR resync path invokes the
    snapshot machinery. Nothing in the capture shows replication, failover or the QM
    reading the LV: it is not open, and it is in no DRBD or Pacemaker resource.
    Whether this counts as ordinary DR operation depending on the snapshot LV is a
    decision for the human (#1425 stop report).
