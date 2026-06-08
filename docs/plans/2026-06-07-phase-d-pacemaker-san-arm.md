# Phase D — Ubuntu Pacemaker/SAN Arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** The self-managed comparison arm (spec §2.3 arm 1, §10-D): a
3-node Ubuntu Corosync/Pacemaker cluster with real STONITH, running a
queue manager on shared iSCSI storage behind a floating IP — driven
through the **identical** §3.1 fault suite as the RDQM arm, plus the
storage-severance drill (step 4) that RDQM's shared-nothing design makes
impossible, then the DRBD-async cross-site DR shape.

**Architecture:** A new severable SAN network carries iSCSI from a
LIO/targetcli target VM (`san-a`) to three arm64 cluster nodes
(`pcmk-a1..a3` — KVM-fast, no TCG: this arm is pure Ubuntu arm64). MQ
data/logs live on the shared LUN; Pacemaker manages a resource group of
Filesystem → IPaddr2 → the Phase-B-style systemd QM unit, with
`fence_virsh` STONITH against the lab hypervisor (the dev VM *is* the
"iLO" of this lab). DR replicates the SAN backing volume to a site-B
target (`san-b`) with host-based DRBD async; cutover is a controlled
promote-and-mount runbook.

**Tech stack:** Ubuntu 24.04 arm64 (Phase A box), corosync/pacemaker/pcs,
targetcli-fb (LIO), open-iscsi, fence-agents (fence_virsh), drbd-utils
(site-B DR), MQ 9.4.5.0 arm64 debs (Phase B tar + roles), Ansible.

**Comparison discipline (spec §10):** 3 cluster nodes to match RDQM
exactly (native odd quorum; the 2-node+qdevice variant is *recorded* as
not built — itself a ledger row: RDQM never offers the cheaper wrong
shape). Same drills, same traffic tool, same persistent-message RPO-0
assertions, same qualitative-timing rules. Every hand-built piece gets a
ledger entry naming its RDQM turnkey equivalent.

**Educational note (issues #32/#33):** this arm is the teaching half of
the lab — quorum, fencing, resource ordering, and shared-storage failure
modes all visible. Keep names explicit and steps narratable; the
walkthrough docs will follow this plan's structure. The longer-term
ambition (overseer, 2026-06-07): publish the lab as a contribution the
MQ community can learn HA/DR with.

---

## Contents

- [Entry gate](#entry-gate)
- [File structure](#file-structure)
  - [Task 1: SAN network + topology nodes](#task-1-san-network--topology-nodes)
  - [Task 2: iSCSI target + initiators (the shared storage RDQM doesn't have)](#task-2-iscsi-target--initiators-the-shared-storage-rdqm-doesnt-have)
  - [Task 3: Cluster formation + STONITH (the part RDQM does with rdqmadm)](#task-3-cluster-formation--stonith-the-part-rdqm-does-with-rdqmadm)
  - [Task 4: MQ on shared storage + the resource group](#task-4-mq-on-shared-storage--the-resource-group)
  - [Task 5: The identical fault suite + the RDQM-impossible drill](#task-5-the-identical-fault-suite--the-rdqm-impossible-drill)
  - [Task 6: Cross-site DR — DRBD async under the SAN (the §2.3 arm-1 DR)](#task-6-cross-site-dr--drbd-async-under-the-san-the-23-arm-1-dr)
  - [Task 7: Findings, convergence, wrap](#task-7-findings-convergence-wrap)
- [Deliberately deferred](#deliberately-deferred)
- [Self-review notes](#self-review-notes)

## Entry gate

```bash
ls /dev/kvm && virsh -c qemu:///system list >/dev/null && echo gate-ok
ls build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz
cd lab && vagrant validate
```

Hypervisor should be empty (Phase C swept). The arm64 MQ tar from Phase B
is the only artifact needed — everything else comes from Ubuntu archives
(these guests are online via the management NAT, unlike the RHEL arm).

## File structure

```text
lab/networks/net-san-a.xml         # NEW: severable SAN net  10.40.1.0/24
lab/networks/net-san-b.xml         # NEW: site-B SAN net     10.40.2.0/24
lab/topology.yaml                  # + pcmk-a1..3, san-a (san-b in DR task)
ansible/roles/iscsi-target/tasks/main.yml      # LIO: backstore, LUN, ACLs
ansible/roles/iscsi-initiator/tasks/main.yml   # open-iscsi login, verify
ansible/roles/pcmk-cluster/tasks/main.yml      # corosync/pacemaker/pcs form
ansible/roles/pcmk-stonith/tasks/main.yml      # fence_virsh per node
ansible/site-pcmk.yml              # the arm's playbook
lab/scripts/pcmk-qm-create.sh      # shared-fs QM + resource group verbs
lab/scripts/pcmk-fault-suite.sh    # drills incl. step 4 (SAN severance)
docs/reports/2026-06-07-phase-d-pacemaker-findings.md
```

IPs: `pcmk-a1..a3` = data-a `10.10.1.51-53`, hb-a `172.16.1.51-53`,
san-a `10.40.1.51-53`, wan `10.99.0.51-53`; `san-a` = san-a `10.40.1.5`,
wan `10.99.0.5`; QM VIP `10.10.1.200`. (Site B mirrors with `.6x`/`.6`
and net-*-b when the DR task lands.)

---

### Task 1: SAN network + topology nodes

**Files:** Create the two `net-san-*.xml` (same isolated shape as the
others); Modify `lab/topology.yaml`.

- [ ] Add `net-san-a`/`net-san-b` XMLs (bridge `virbr-san-a/b`,
  `10.40.1.1`/`10.40.2.1`, no forward, no DHCP).
- [ ] Topology: `san-a` (1 cpu, 1024M, `extra_disk: 8` — the LUN backing,
  nics: net-san-a `10.40.1.5`, net-wan `10.99.0.5`); `pcmk-a1..a3`
  (2 cpu, 2048M, nics per the IP table; platform default = the arm64
  Ubuntu box; **no extra disk — storage is the SAN's job**).
- [ ] `lab/scripts/net-up.sh` picks the new XMLs up automatically;
  `vagrant validate`; boot all four (`vagrant up pcmk-a1 pcmk-a2 pcmk-a3
  san-a`) — arm64/KVM, expect ~90s total. Extend `ansible/inventory.sh`
  with `pcmk_a`/`san_hosts` pattern groups. Commit.

### Task 2: iSCSI target + initiators (the shared storage RDQM doesn't have)

**Files:** Create `ansible/roles/iscsi-target/tasks/main.yml`,
`ansible/roles/iscsi-initiator/tasks/main.yml`, `ansible/site-pcmk.yml`.

- [ ] **Target role** (san-a): apt `targetcli-fb`; idempotent targetcli
  script — block backstore on `/dev/vdb`, IQN
  `iqn.2026-06.lab.mq:san-a.lun0`, LUN 0, ACLs for the three initiator
  IQNs, portal on `10.40.1.5:3260`; `targetctl save`. (Idempotence: guard
  on `targetcli ls /backstores/block` output.)
- [ ] **Initiator role** (pcmk nodes): apt `open-iscsi`; set a stable
  InitiatorName per node (`iqn.2026-06.lab.mq:pcmk-aN`); discovery +
  login to the portal; `node.startup = automatic`. Verify the same LUN
  appears as `/dev/sd?` on all three (`lsblk` serial match).
- [ ] **One-time fs prep** (run-once task on a1): `mkfs.xfs` the LUN,
  label `MQSHARED`. **Never mounted by more than one node — Pacemaker
  owns the mount from Task 4 on.** Commit.

### Task 3: Cluster formation + STONITH (the part RDQM does with rdqmadm)

**Files:** Create `ansible/roles/pcmk-cluster/tasks/main.yml`,
`ansible/roles/pcmk-stonith/tasks/main.yml`.

- [ ] **Cluster role:** apt `pacemaker corosync pcs`; set `hacluster`
  password (runtime env, no_log — the mqweb-creds pattern); `pcs host
  auth` + `pcs cluster setup mqpcmk pcmk-a1 addr=172.16.1.51 pcmk-a2
  addr=172.16.1.52 pcmk-a3 addr=172.16.1.53` (corosync ring on the
  heartbeat net — same net the severance drill cuts); enable+start;
  verify `pcs status` shows 3 nodes online. Idempotence: guard on
  `/etc/corosync/corosync.conf` containing the cluster name.
- [ ] **STONITH role:** apt `fence-agents-base` (+virsh agent package —
  verify the Ubuntu package name at execution: `fence-agents` ships
  fence_virsh on noble); a dedicated ssh keypair in `build/` (gitignored)
  authorized for the dev-VM user; one `stonith:fence_virsh` primitive per
  node (`pcmk_host_list=<node>`, `ip=192.168.121.1`, `login=<user>`,
  `identity_file=...`, `port=lab_<node>` — the libvirt domain name);
  location constraints so a node never runs its own fence device;
  `stonith-enabled=true`.
- [ ] **Fence proof** (the educational money-shot): `pcs stonith fence
  pcmk-a3` → the hypervisor hard-resets that exact domain; node reboots
  and rejoins. Record in findings. Commit.

### Task 4: MQ on shared storage + the resource group

**Files:** Create `lab/scripts/pcmk-qm-create.sh`; Modify
`ansible/site-pcmk.yml` (mq-install play reuse).

- [ ] **MQ install:** reuse Phase B's `mq-install` role verbatim on the
  three nodes (same tar, same debs — the role was built for this).
- [ ] **QM on the LUN** (script, narratable): on a1 only — mount LUN at
  `/mqshared`, `crtmqm -md /mqshared/qmgrs -ld /mqshared/log QMPCMK`,
  define the lab objects (listener 1414 CONTROL(QMGR), APP.SVRCONN with
  MCAUSER mqm, CHLAUTH relaxed, `HA.TEST` DEFPSIST(YES)), `endmqm`,
  unmount. `dspmqinf -o command` export + `addmqinf` on a2/a3 so all
  nodes know the QM definition.
- [ ] **Resource group** (the hand-built equivalent of `crtmqm -sx`):

```bash
pcs resource create mq_fs ocf:heartbeat:Filesystem \
  device=/dev/disk/by-label/MQSHARED directory=/mqshared fstype=xfs
pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip=10.10.1.200 cidr_netmask=24
pcs resource create mq_qm systemd:mq-QMPCMK
pcs resource group add mq_group mq_fs mq_vip mq_qm
```

  (`mq-QMPCMK.service` = the Phase B template, deployed disabled —
  Pacemaker starts it, never systemd.)
- [ ] Verify: group runs on one node; `amqsputc`/`amqsgetc` via
  `10.10.1.200` from a non-owner node; `pcs resource move` walks the
  whole group (fs→VIP→QM order visible). Commit.

### Task 5: The identical fault suite + the RDQM-impossible drill

**Files:** Create `lab/scripts/pcmk-fault-suite.sh`; findings rows per
drill.

Same discipline as Phase C: persistent messages seeded pre-fault via the
VIP, retrieved post-fault via the VIP; RPO-0 assertion; qualitative
timing; ground-truth checks (`pcs status`, exactly-one-owner assertion).

- [ ] **Drill 1** `kill -9` QM processes on the owner → monitor restarts
  it (systemd resource recovery) or migrates; messages intact.
- [ ] **Drill 2** `virsh destroy` the owner → **STONITH confirms the
  kill**, group migrates, VIP follows; power node back, rejoins.
- [ ] **Drill 3** sever the owner's heartbeat NIC → quorum side fences
  the isolated node (watch fence_virsh actually fire), exactly one owner
  throughout, no split-brain, no double-mount (the XFS corruption
  scenario this arm risks and RDQM cannot have).
- [ ] **Drill 4 (NEW — spec §3.1 step 4, RDQM N/A):** sever the owner's
  **SAN NIC** → Filesystem resource fails, group recovers on a node with
  storage; document behavior + any I/O-error window. *This is the
  storage-SPOF made visible — the §2.5 Q4 evidence.*
- [ ] **Drill 5** planned move + failback (`pcs resource move` / clear).
- [ ] Commit suite + findings rows, each naming the RDQM comparison.

### Task 6: Cross-site DR — DRBD async under the SAN (the §2.3 arm-1 DR)

- [ ] Topology: `san-b` (net-san-b `10.40.2.6`, net-wan `10.99.0.6`,
  extra_disk 8) + `pcmk-b1..b3` mirroring site A. Boot; same roles form
  site B's cluster + target (ACLs for b-node IQNs).
- [ ] **DRBD between the SANs:** drbd-utils on san-a/san-b; resource
  `mqlun` over net-wan (`10.99.0.5` ↔ `10.99.0.6`), protocol A (async),
  backing `/dev/vdb`; LIO exports the **drbd device** (`/dev/drbd0`)
  instead of raw vdb at both sites (re-point san-a's backstore; site B's
  target configured but LUN masked while secondary).
- [ ] **Controlled cutover runbook** (the §8.5 paved path, hand-built):
  quiesce QM at A → unmount → demote drbd at A / promote at B → unmask
  site-B LUN → site-B cluster mounts, `addmqinf`, starts the group (VIP
  `10.10.2.200`) → messages put at A pre-cutover retrieved at B (RPO 0
  for a *controlled* cutover; the async-window caveat documented for the
  disaster variant). Then failback the mirror image.
- [ ] Findings: step count + manual-coordination surface vs `rdqmdr`'s
  two commands — the ledger's sharpest row.

### Task 7: Findings, convergence, wrap

- [ ] Complete `docs/reports/2026-06-07-phase-d-pacemaker-findings.md`:
  drill table, the component-by-component ledger (each hand-built piece →
  its RDQM equivalent → Day-2 surface), the §2.5 Q1/Q4 answers
  (parity: achieved or not, and at what operational cost).
- [ ] Convergence re-runs (plays + scripts), `vrg-validate`, final
  commit, `.vergil/pr-template.yml`.

---

## Deliberately deferred

- Booth ticket arbitration for *automated* cross-site failover — the
  controlled runbook demonstrates the DR mechanics; Booth adds an
  arbitrator and automation Phase E can weigh as further complexity
  without needing it built.
- Multipath I/O, target redundancy — single-path lab SAN; the SPOF is
  the point, not a flaw to engineer away here (§2.5 Q4).
- The 2-node+qdevice quorum variant — recorded, not built.

## Self-review notes

- **Spec coverage:** §2.3 arm 1 components all present (Pacemaker/
  Corosync OCF-managed QM, SAN/iSCSI, STONITH, DR via DRBD async);
  §10-D's "identical §3.1 suite" honored plus step 4; §8.4 verbs mirror
  the RDQM scripts (`pcmk-qm-create.sh` ↔ `rdqm-qm-create.sh`).
- **Trust-but-verify checkpoints:** Ubuntu noble's fence_virsh package
  name; targetcli idempotence probe; `dspmqinf/addmqinf` exact flags;
  DRBD-under-LIO re-pointing. Each stops and reads the tool, per the
  pattern that worked all week.
- **Phase B/C lessons pre-applied:** single install pass, persistent
  messages only, no pipes before exit codes, no stderr suppression on
  assertions, bounded foreground waits, absolute paths in bg commands,
  volume sweeps after destroys, ledger rows written at discovery time.
