# RDQM 3-Node HA — Setup Cheat Sheet

> Scope: IBM **MQ 9.4.5** Advanced for Developers on **RHEL 9.6**, RDQM **HA
> group** across 3 nodes. RDQM bundles and manages the DRBD + Pacemaker stack
> itself — you configure RDQM, not DRBD/Pacemaker directly.
>
> Lab IPs below are placeholders — **substitute your site's addresses.**
>
> Sources: `ansible/roles/rdqm-install`, `ansible/roles/rdqm-ha`,
> `lab/scripts/rdqm-qm-create.sh`, `docs/reference/lab-gotchas.md`,
> `docs/reports/2026-06-06-phase-c-rdqm-findings.md`.
>
> **Not RDQM:** the DRBD-by-hand commands elsewhere in this repo (`drbdadm`,
> `pcs resource`, resource `mqlun`) belong to the separate manual Pacemaker/SAN
> HA arm — don't use them here.

## 0. Per-node prerequisites (all 3 nodes)

| Item | Requirement |
|---|---|
| OS | RHEL 9.6 x86_64 |
| Spare disk | e.g. `/dev/vdb` ≥ 10 GB, **unpartitioned**, for the `drbdpool` VG |
| Replication NIC | A dedicated subnet/interface per node (the `HA_Replication` address) |
| Data NIC | Subnet that carries the QM listener (port 1414) + floating IP |
| Name resolution | `/etc/hosts` entries for all 3 node names (no DNS needed) |

## 1. Install MQ + RDQM stack — one pre-QM pass, RDQM package LAST

Two hard ordering rules (both encoded in `rdqm-install/tasks/main.yml`):

1. Install **everything before creating any queue manager** — MQ's preinst
   refuses changes once QM shared memory is live.
2. Install `MQSeriesRDQM` in its **own** `dnf` transaction, *after* the server
   files exist — its preinst scriptlet needs them on disk.

```bash
# Accept the developer licence
/tmp/MQServer/mqlicense.sh -accept

# Pick the DRBD kmod that EXACTLY matches the running kernel (no fuzzy match)
KREL=$(uname -r); BASE=${KREL%%.el9*}
KMOD=$(ls /tmp/MQServer/Advanced/RDQM/PreReqs/el9/kmod-drbd-9/kmod-drbd-*${BASE//-/_}-*.rpm)

# Step 1 of 2 — MQ + cluster prereqs (pacemaker, drbd-utils, kmod)
cd /tmp/MQServer
dnf install -y \
  MQSeriesRuntime-*.rpm MQSeriesServer-*.rpm MQSeriesGSKit-*.rpm \
  MQSeriesJava-*.rpm MQSeriesJRE-*.rpm MQSeriesWeb-*.rpm \
  MQSeriesSDK-*.rpm MQSeriesClient-*.rpm \
  Advanced/RDQM/PreReqs/el9/pacemaker-2/*.rpm \
  Advanced/RDQM/PreReqs/el9/drbd-utils-9/*.rpm \
  "$KMOD"

# Step 2 of 2 — RDQM package, separately
dnf install -y Advanced/RDQM/MQSeriesRDQM-*.rpm

# Post-install
/opt/mqm/bin/setmqinst -i -p /opt/mqm    # set primary installation (loader path)
modprobe drbd                            # confirm the module loads on this kernel

# LVM volume group RDQM will carve QM storage from
vgs drbdpool >/dev/null 2>&1 || { pvcreate /dev/vdb && vgcreate drbdpool /dev/vdb; }

# CRITICAL: mqm must be in haclient, or endmqm/strmqm under HA control fail AMQ7077E
usermod -aG haclient mqm

# mqm ulimits
printf 'mqm soft nofile 10240\nmqm hard nofile 10240\n' > /etc/security/limits.d/99-mqm.conf

# Verify
/opt/mqm/bin/dspmqver
```

## 2. Form the RDQM HA group (all 3 nodes)

`/etc/hosts` on every node (use the **data** addresses):

```
10.10.1.31  rdqm-a1
10.10.1.32  rdqm-a2
10.10.1.33  rdqm-a3
```

`/var/mqm/rdqm.ini` — **identical on all 3 nodes**, owned `mqm:mqm`.
`HA_Replication` is each node's *replication-network* address:

```ini
Node:
  Name=rdqm-a1
  HA_Replication=172.16.1.31
Node:
  Name=rdqm-a2
  HA_Replication=172.16.1.32
Node:
  Name=rdqm-a3
  HA_Replication=172.16.1.33
```

Initialize the replicated-data subsystem on each node:

```bash
/opt/mqm/bin/rdqmadm -c
/opt/mqm/bin/rdqmstatus -n     # verify all 3 nodes online
```

## 3. Create the replicated queue manager — secondaries first, then primary

Verified order from `lab/scripts/rdqm-qm-create.sh`. All `crtmqm` run **as root**.

```bash
QM=QMRDQM
VIP=10.10.1.100        # floating IP clients connect to

# 1) Secondaries FIRST (on rdqm-a2 and rdqm-a3)
/opt/mqm/bin/crtmqm -fs 3072M -sxs $QM

# 2) Primary (on rdqm-a1)
/opt/mqm/bin/crtmqm -sx -fs 3072M $QM

# 3) Floating IP on the primary, bound to the data-subnet interface
IFACE=$(ip -br addr | grep "${VIP%.*}." | cut -d' ' -f1)
/opt/mqm/bin/rdqmint -m $QM -a -f $VIP -l $IFACE
```

`crtmqm` flags: `-sx` = create HA **primary**; `-sxs` = create HA **secondary**;
`-fs` = filesystem size.

Configure listener/channel (lab posture — adjust security for production):

```bash
su mqm -c "/opt/mqm/bin/runmqsc $QM" <<'EOF'
DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE
START LISTENER(L1414)
DEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('mqm') HBINT(15) KAINT(15) REPLACE
ALTER QMGR CHLAUTH(DISABLED) CONNAUTH(' ')
REFRESH SECURITY TYPE(CONNAUTH)
DEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE
EOF
```

> ⚠️ `CHLAUTH(DISABLED)` + `MCAUSER('mqm')` is **lab-only**. Don't carry that to
> a production QM.

## 4. Verify

```bash
/opt/mqm/bin/rdqmstatus -m QMRDQM
```

Expect one node `Running / Primary / Synced` and two `Replicated / Secondary /
Synced`.

## 5. Everyday operations

| Action | Command |
|---|---|
| HA status (whole group) | `rdqmstatus -n` |
| HA status (one QM, all nodes) | `rdqmstatus -m QMRDQM` |
| Is QM up on this node | `dspmq -m QMRDQM` |
| Planned move off a node | suspend/resume via `rdqmadm -s` / `rdqmadm -r` |
| Add / remove floating IP | `rdqmint -m QMRDQM -a -f <ip> -l <iface>` / `-r` |

## 6. Gotchas (each cost real time — see `lab-gotchas.md` / Phase-C findings)

1. **`mqm` ∉ `haclient` → AMQ7077E** on `endmqm`/`strmqm` under HA. Fix is the
   `usermod -aG haclient mqm` step above.
2. **Combined RPM transaction fails** — RDQM package must install *after* MQ
   server files. Two separate `dnf install`.
3. **DRBD kmod must match the kernel exactly** — no fuzzy match; mismatch fails
   loud. If `modprobe drbd` fails, you grabbed the wrong kmod.
4. **`drbdpool` VG must exist before any QM** — RDQM carves QM storage from it.
5. **HA-only cannot be converted in place to HA/DR** — if you'll ever need
   cross-site DR, create the QM with the DR flags from the start (HA-only →
   HA/DR means delete + recreate, losing QM contents).

## 7. Optional: HA/DR (3+3, two sites)

Verified in Phase-C drills, but only if you need cross-site DR.

Create with DR roles instead of plain HA: `crtmqm -sx -rr p …` at the primary
site, `-rr s …` at the recovery site (DR replication over a WAN link).
Controlled cutover is `rdqmdr -s` at the old primary then `rdqmdr -p` at the
recovery site; re-add the floating IP at the recovery site. Drills measured
~69 s cutover / ~104 s failback at **RPO 0**. Decide HA-only vs HA/DR *before*
`crtmqm` (gotcha #5).
