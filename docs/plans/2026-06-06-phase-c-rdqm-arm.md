# Phase C — RDQM Arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** The comparison-baseline arm (spec §10-C): a 3-node synchronous
RDQM HA group at site A with a floating IP, exercised by the §3.1 fault
primitives, then extended to the documented 3+3 HA/DR shape with `rdqmdr`
cutover/failback to site B — on RHEL 9.6 x86-64 guests under TCG.

**Architecture:** A kickstart-built RHEL 9.6 vagrant-libvirt box (from the
DVD ISO — fully offline, no Red Hat connectivity at install or run time)
joins `topology.yaml` as a second platform. An `rdqm-install` Ansible role
installs MQ + the RDQM stack from the no-charge Developers tar (verified:
`MQSeriesRDQM` rpm, LINBIT Pacemaker/corosync, `drbd-utils`, and prebuilt
`kmod-drbd` for el9 kernels `5.14.0-284.x` → `611.x` all ship in it).
HA formation is `rdqm.ini` + `rdqmadm` + `crtmqm -sx`; cluster nodes carry
a dedicated second disk for the `drbdpool` volume group.

**Tech stack:** RHEL 9.6 (DVD ISO + kickstart/OEMDRV), IBM MQ Advanced for
Developers 9.4.5.0 LinuxX64, RDQM (DRBD 9.2.16 + Pacemaker 2/LINBIT),
vagrant-libvirt (TCG x86: q35 + `cpu_mode maximum` per #24), Ansible.

**Scope honesty (spec §6):** six TCG-emulated x86 guests on 12 cores —
**functional correctness only**; RTO wall-clocks are reported
qualitatively and never compared against arm64 numbers. Bring site A up
and validated before site B exists. No cluster-timer tuning to mask
emulation jitter; spurious fencing = stop and surface (the §6 wire).

---

## Entry gate

```bash
ls build/rhel-9.6-x86_64-dvd.iso build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz
ls /dev/kvm && virsh -c qemu:///system list >/dev/null && echo gate-ok
cd lab && vagrant validate
```

Both artifacts live in gitignored `build/` (12.7 GB ISO, 520 MB tar) —
neither ever enters git. Phase A/B guests should be torn down (CPU budget).

## File structure

```text
lab/boxes/rhel96/ks.cfg            # kickstart (vagrant user, minimal, serial)
lab/boxes/rhel96/build-box.sh      # ISO -> unattended install -> .box
lab/topology.yaml                  # + rhel96-x86_64 platform, 6 rdqm nodes (modify)
lab/Vagrantfile                    # + per-node extra disk support (modify)
ansible/roles/rdqm-install/tasks/main.yml      # rpms, kmod match, drbdpool
ansible/roles/rdqm-ha/tasks/main.yml           # rdqm.ini, rdqmadm, firewall-off
ansible/roles/rdqm-ha/templates/rdqm.ini.j2
ansible/site-rdqm.yml              # the RDQM arm playbook (separate from B's)
lab/scripts/rdqm-fault-suite.sh    # 3.1 steps 1/2/3/5 against the HA group
docs/reports/2026-06-XX-phase-c-rdqm-findings.md   # written as evidence lands
```

IP allocations (site-A nodes `.31-.33`, site-B `.41-.43`, VIPs `.100`):

| node | net-data-* | net-hb-* | net-wan |
|---|---|---|---|
| rdqm-a1..a3 | 10.10.1.31-33 | 172.16.1.31-33 | 10.99.0.31-33 |
| rdqm-b1..b3 | 10.10.2.41-43 | 172.16.2.41-43 | 10.99.0.41-43 |
| QM floating IP | site A 10.10.1.100 / site B 10.10.2.100 | | |

---

### Task 1: RHEL 9.6 box build (kickstart, fully offline)

**Files:** Create `lab/boxes/rhel96/ks.cfg`, `lab/boxes/rhel96/build-box.sh`.

- [ ] **Step 1: The kickstart**

```text
# lab/boxes/rhel96/ks.cfg - minimal RHEL 9.6 for the RDQM lab box.
# Installed from the DVD ISO; no Red Hat connectivity required (spec 6
# offline premise). Security posture is lab-grade per spec 1.
text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
rootpw --lock
network --bootproto=dhcp --device=link --activate
bootloader --location=mbr --append="console=ttyS0,115200n8"
zerombr
clearpart --all --initlabel
autopart --type=plain --nohome
firewall --disabled
selinux --enforcing
services --enabled=sshd
user --name=vagrant --password=vagrant --plaintext --groups=wheel
sshkey --username=vagrant "ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAQEA6NF8iallvQVp22WDkTkyrtvp9eWW6A8YVr+kz4TjGYe7gHzIw+niNltGEFHzD8+v1I2YJ6oXevct1YeS0o9HZyN1Q9qgCgzUFtdOKLv6IedplqoPkcmF0aYet2PkEDo3MlTBckFXPITAMzF8dJSIFo9D8HfdOV0IAdx4O7PtixWKn5y2hMNG0zQPyUecp4pzC6kivAIhyfHilFR61RGL+GPXQ2MWZWFYbAGjyiYJnAmCP3NOTd0jMZEnDkbUvxhMmBYSdETk1rRgm+R4LOzFUGaHqHDLKLX+FIPKcF96hrucXzcWyLbIbEgE98OHlnVYCzRdK8jlqm8tehUc9c9WhQ== vagrant insecure public key"
poweroff
%packages
@^minimal-environment
openssh-server
lvm2
%end
%post
echo 'vagrant ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/vagrant
chmod 440 /etc/sudoers.d/vagrant
%end
```

(The vagrant insecure public key is deliberately public/well-known;
vagrant replaces it per-machine at first boot.)

- [ ] **Step 2: The build script**

```bash
#!/usr/bin/env bash
# lab/boxes/rhel96/build-box.sh - unattended RHEL 9.6 box build:
# DVD ISO + OEMDRV kickstart -> qcow2 -> vagrant-libvirt .box.
# One-time cost ~45-90 min under TCG. Requires the ISO in build/.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"
ISO="$ROOT/build/rhel-9.6-x86_64-dvd.iso"
WORK="$ROOT/build/rhel96-box"; mkdir -p "$WORK"
test -f "$ISO"

# 1. OEMDRV volume: anaconda auto-loads ks.cfg from a volume so labeled.
genisoimage -quiet -V OEMDRV -o "$WORK/oemdrv.iso" ks.cfg

# 2. Blank disk + transient build domain (the #24 TCG recipe).
sudo qemu-img create -f qcow2 /var/lib/libvirt/images/rhel96-build.qcow2 20G
sudo cp "$WORK/oemdrv.iso" /var/lib/libvirt/images/oemdrv.iso
sed -e "s|@ISO@|$ISO|" build-domain.xml.tpl > "$WORK/domain.xml"
virsh -c qemu:///system define "$WORK/domain.xml"
virsh -c qemu:///system start rhel96-build
echo "installing (TCG, expect 45-90 min); waiting for poweroff..."
until [ "$(virsh -c qemu:///system domstate rhel96-build)" = "shut off" ]; do sleep 60; done

# 3. Package the box.
sudo qemu-img convert -O qcow2 -c /var/lib/libvirt/images/rhel96-build.qcow2 "$WORK/box.img"
sudo chown "$(id -u)" "$WORK/box.img"
printf '{"provider":"libvirt","format":"qcow2","virtual_size":20}\n' > "$WORK/metadata.json"
tar -C "$WORK" -czf "$ROOT/build/rhel-9.6-x86_64-libvirt.box" metadata.json box.img
vagrant box add --force rhel/9.6-x86_64 "$ROOT/build/rhel-9.6-x86_64-libvirt.box"

# 4. Cleanup.
virsh -c qemu:///system undefine rhel96-build
sudo rm -f /var/lib/libvirt/images/rhel96-build.qcow2 /var/lib/libvirt/images/oemdrv.iso
echo "box ready: rhel/9.6-x86_64"
```

Plus `build-domain.xml.tpl`: the proven diag-x86 domain XML (q35,
`<cpu mode='maximum'/>`, 4 vCPU, 4096 MiB, virtio disk
`rhel96-build.qcow2`, the DVD ISO and `oemdrv.iso` as two cdroms, serial
console to file, boot order cdrom-then-disk, `on_poweroff: destroy`).
ISO path is host-mounted — verify qemu (uid 64055) can read `build/`;
if not, copy the ISO into the storage pool first (the diag spike's
permission lesson).

- [ ] **Step 3: Run it; verify the box boots via vagrant**

```bash
lab/boxes/rhel96/build-box.sh           # background; serial log shows anaconda
# then a throwaway: vagrant init rhel/9.6-x86_64 in /tmp + the x86 stanza
# from lab/Vagrantfile; expect SSH-ready; uname -r is the GA 9.6 kernel
# (5.14.0-570.x family) -> within the shipped kmod matrix.
```

**Checkpoint:** record `uname -r` in the findings report — Task 3's kmod
selection consumes it. If the kernel is outside the shipped kmod list,
stop and surface (options: pin older kernel from the DVD, or check the
`modver` manifest for kABI-compatible coverage).

- [ ] **Step 4: Commit** (`feat(lab): kickstart-built RHEL 9.6 vagrant box`)

### Task 2: Topology — six RDQM nodes + extra disk support

**Files:** Modify `lab/topology.yaml`, `lab/Vagrantfile`.

- [ ] **Step 1:** Add platform + nodes. Platform:
`rhel96-x86_64: { box: rhel/9.6-x86_64, arch: x86_64 }`. Nodes
`rdqm-a1..a3` / `rdqm-b1..b3` per the IP table (nics: data, hb, wan),
each with `cpus: 2, memory: 2048, extra_disk: 10` — and in the
Vagrantfile, render `extra_disk` as
`lv.storage :file, :size => "#{spec['extra_disk']}G"` (becomes `/dev/vdb`,
the future `drbdpool` PV).

- [ ] **Step 2:** `vagrant validate`; boot **site A only**
(`vagrant up rdqm-a1 rdqm-a2 rdqm-a3`); all three SSH-reachable; commit.
(Site B boots in Task 6 — CPU budget discipline.)

### Task 3: `rdqm-install` role (rpms, kmod match, drbdpool)

**Files:** Create `ansible/roles/rdqm-install/tasks/main.yml`,
`ansible/site-rdqm.yml`; extend `ansible/inventory.sh` with an
`[rdqm_a]`/`[rdqm_b]` group rendering.

- [ ] **Step 1:** Role outline (single pre-QM pass — the Phase B lesson):

```yaml
# 1. copy + unpack the LinuxX64 tar (creates: /tmp/MQServer)
# 2. mqlicense.sh -accept              (creates: /var/mqm)
# 3. dnf install MQSeries{Runtime,Server,GSKit,Java,JRE,Web,SDK,Client}*.rpm
#    + Advanced/RDQM/PreReqs/el9/pacemaker-2/*.rpm
#    + Advanced/RDQM/PreReqs/el9/drbd-utils-9/*.rpm
#    + the ONE kmod-drbd matching `uname -r` (fact-driven file glob;
#      fail loudly naming the kernel if no match - no silent fallback)
#    + Advanced/RDQM/MQSeriesRDQM-*.rpm
#    (creates: /opt/mqm/bin/rdqmadm)
# 4. setmqinst -i -p /opt/mqm          (the Phase B loader lesson)
# 5. drbdpool VG on the extra disk:  pvcreate /dev/vdb && vgcreate drbdpool /dev/vdb
#    (creates-guard via vgs)
# 6. systemctl disable --now firewalld (lab posture, spec 1)
# 7. mqm ulimits (as Phase B)
```

- [ ] **Step 2:** Run against site A (`ansible-playbook site-rdqm.yml
--limit rdqm_a`); verify `dspmqver` = 9.4.5.0, `lsmod | grep drbd` after
`modprobe drbd`, `vgs drbdpool` on all three. Re-run = no changes. Commit.

### Task 4: HA group formation + floating IP

**Files:** Create `ansible/roles/rdqm-ha/` (tasks + `rdqm.ini.j2`).

- [ ] **Step 1:** `rdqm.ini` (heartbeat/replication on net-hb-a):

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

Role: template to `/var/mqm/rdqm.ini` on all three; `rdqmadm -c` on each
(idempotent: skip when `rdqmstatus -n` reports the cluster); hosts file
entries for the three names (no DNS in the lab).

- [ ] **Step 2:** Create the replicated QM + VIP (on the current primary,
a1): `crtmqm -sx -fs 3072M QMRDQM`, then
`rdqmint -m QMRDQM -a -l 10.10.1.100` (floating IP on the data net).
**Trust-but-verify checkpoint:** exact `crtmqm -sx`/`rdqmint` flags
re-checked against the 9.4 docs at execution; the shape (sync 3-node, VIP
follows the QM) is fixed by spec §4.4.

- [ ] **Step 3:** Verify: `rdqmstatus -m QMRDQM` shows the primary +
two synchronized secondaries; from the dev VM, `ping 10.10.1.100` and a
pymqi put/get against the VIP (reuse Phase B's APP.SVRCONN + content
pattern via a minimal `content/qmrdqm.yaml` — REST/mqweb per-node comes
with it, the §8.3 stateless-mqweb-behind-VIP architecture in its first
real exercise). Commit.

### Task 5: §3.1 fault suite, steps 1/2/3/5 (the harness meets its purpose)

**Files:** Create `lab/scripts/rdqm-fault-suite.sh`; findings report grows
a results table per drill.

- [ ] **Step 1 (suite step 1):** `kill -9` the QM processes on the primary
→ expect restart/failover per RDQM design; record behavior + qualitative
RTO; messages put pre-kill (persistent!) survive — RPO 0 assertion.
- [ ] **Step 2 (suite step 2):** `virsh destroy rdqm-a1` (hard power-off)
→ QM + VIP move to a survivor; put/get against the VIP succeeds; power
the node back on, confirm it rejoins and resynchronizes (`rdqmstatus`).
- [ ] **Step 3 (suite step 3):** sever the **heartbeat NIC** on the
primary (the Phase A `domif-setlink` primitive, 2s settle) → confirm
quorum behavior, NO split-brain (exactly one node runs the QM throughout
— assert via `rdqmstatus` on all three), restore, resync.
- [ ] **Step 4 (suite step 5):** planned failover and **failback**
(`rdqmadm -s` suspend primary → QM moves; resume → controlled return).
- [ ] **Step 5:** Each drill appends a row (drill, expected, observed,
intervention?, anomalies) to the findings report. Commit suite + results.

### Task 6: Site B + the 3+3 DR pair

- [ ] **Step 1:** Boot `rdqm-b1..b3`; run `site-rdqm.yml --limit rdqm_b`;
form site B's HA group (same roles, site-B vars). Commit any role
generalization needed.
- [ ] **Step 2:** Recreate the QM as the documented **HA/DR combined**
shape (spec §4.4: sync HA group per site, async DR between them over
`net-wan`): per 9.4 docs, `crtmqm` with `-sx` + the DR replication
options (`-rr p` primary at site A, the site-B group created `-rr s`;
exact flag set is a trust-but-verify checkpoint against the
"RDQM disaster recovery and high availability" doc page). DR replication
addresses = the `net-wan` IPs.
- [ ] **Step 3:** Verify async replication catches up
(`rdqmstatus -m QMRDQM` DR fields), then **cutover**: quiesce at A,
confirm replication current, `rdqmdr` make-secondary at A /
make-primary at B → QM runs at site B (VIP 10.10.2.100); pymqi put/get
there. Then **failback** the same controlled way. This is §8.5's
"confirm caught up before you cut" paved path, executed manually once —
scripting it is Phase E/F material.
- [ ] **Step 4:** Findings report: DR section (what was manual, where the
async window showed, qualitative timings). Commit.

### Task 7: Wrap — convergence, findings report, handoff

- [ ] Re-run `site-rdqm.yml` (no failures, converging), re-run fault
suite spot-checks, finalize
`docs/reports/2026-06-XX-phase-c-rdqm-findings.md` (incl. the §2.5 Q2
evidence: what RDQM gave turnkey vs what Phases A/B had to hand-build —
the apples-to-apples ledger Phase E consumes), `vrg-validate`, final
commit, `.vergil/pr-template.yml`.

---

## Deliberately deferred

- **Fault-suite steps 4/6/8** (storage severance, rolling patch,
  `runmqras` diagnostics capture) — Phase E/F per spec §8.7; the RDQM
  arm's storage is node-local by design (no SAN to sever — itself a
  finding for the ledger).
- **Step 9 site-role rotation** as a paved-path script — §8.5, Phase F.
- **REST/content full parity on RDQM QM** — minimal put/get + VIP-REST
  proof here; full content-plane parity when Phase D needs the identical
  operator verbs for comparison.
- **Guest service minimization** (§7.2) — now actually relevant (6 TCG
  guests), but it changes the box build; revisit if site-B boot times
  prove painful rather than pre-optimizing.

## Self-review notes

- **Spec coverage:** §10-C's "3-node sync HA + async DR 3+3 + rdqmdr
  cutover/failback + fault suite incl. full-site-loss" — Tasks 4-6 (full
  site loss = site A powered off during cutover validation is *not*
  claimed; the controlled cutover is; the disaster-variant joins the
  full suite in Phase E prep). §8.4's `form-group` verbs exist as roles;
  §4.4's no-stretched-group rule is respected (HA inside sites, DR
  across).
- **Trust-but-verify checkpoints, not placeholders:** exact
  `crtmqm -rr`/`rdqmint`/`rdqmdr` flag spellings, kmod kABI match, and
  the ISO-read permission each have a named verification step.
- **Phase B lessons pre-applied:** single rpm pass before any QM;
  `setmqinst -i`; persistent messages in any traffic tests; loud failure
  when the kmod doesn't match; no `2>/dev/null` over assertion commands;
  no pipes in front of exit codes.
