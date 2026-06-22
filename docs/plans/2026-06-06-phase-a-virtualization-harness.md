# Phase A — Virtualization Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible Vagrant + libvirt harness inside the dev VM that
stands up the two-DC lab topology (3+3 nodes, severable multi-NIC networks,
{os,arch}-parameterized) — entered through the spec §7.2 provider spike.

**Architecture:** Pre-created libvirt networks (XML, script-managed) carry the
topology; a single multi-machine `Vagrantfile` reads a declarative
`topology.yaml` and attaches each node to its networks by name. arm64 guests
run KVM-accelerated (nested virt); x86-64 guests run TCG-emulated. The spike
(Tasks 1–4) proves each load-bearing assumption before the fabric (Tasks 5–8)
is built on it.

**Tech stack:** vagrant + vagrant-libvirt, libvirt/QEMU (KVM + TCG), virsh,
YAML topology config, bash smoke tests. No Ansible yet — host configuration
enters in Phase B (spec §7.3); this phase is bare harness only.

**Design references:** spec §5 (topology), §6 (build matrix), §7.1–7.2
(harness + provider bind), §10-A (phase definition).

---

## Contents

- [Entry gate (verify before Task 1)](#entry-gate-verify-before-task-1)
- [File structure](#file-structure)
  - [Task 1: Spike — libvirt + KVM sanity](#task-1-spike--libvirt--kvm-sanity)
  - [Task 2: Spike — arm64 guest boots KVM-accelerated](#task-2-spike--arm64-guest-boots-kvm-accelerated)
  - [Task 3: Spike — x86-64 guest boots under TCG](#task-3-spike--x86-64-guest-boots-under-tcg)
  - [Task 4: Spike — severable multi-NIC networking + report](#task-4-spike--severable-multi-nic-networking--report)
  - [Task 5: Network fabric — the full topology net set](#task-5-network-fabric--the-full-topology-net-set)
  - [Task 6: Topology config + parameterized Vagrantfile](#task-6-topology-config--parameterized-vagrantfile)
  - [Task 7: Boot the 3+3 skeleton + smoke test](#task-7-boot-the-33-skeleton--smoke-test)
  - [Task 8: Reproducibility proof + spike cleanup](#task-8-reproducibility-proof--spike-cleanup)
- [Deliberately deferred (recorded so they are decisions, not omissions)](#deliberately-deferred-recorded-so-they-are-decisions-not-omissions)
- [Self-review notes](#self-review-notes)

## Entry gate (verify before Task 1)

All four were broken at least once during Phase 0/interim work — do not skip.

```bash
ls -l /dev/kvm                      # needs vergil.toml nested = true (#14)
virsh -c qemu:///system list --all  # needs libvirt/kvm groups (vergil-vm#137)
vagrant plugin list                 # vagrant-libvirt present, no bsdtar warning
cloud-init status                   # done (no error)
```

Expected: all four clean. If any fails, **stop** — fix the VM profile
(`vergil.toml` + `vrg-vm rebuild`) or the vergil-vm provisioning first;
nothing below works without them.

> Session quirk: if `vrg-container-run` fails with "rootless containerd not
> running", export `XDG_RUNTIME_DIR=/run/user/$(id -u)` first.

## File structure

```text
lab/
  Vagrantfile               # multi-machine, reads topology.yaml
  topology.yaml             # boxes, defaults, sites, nodes, NIC/IP map
  networks/
    net-svc.xml            # SVC-facing net          10.20.0.0/24
    net-client.xml          # client/app net           10.30.0.0/24
    net-wan.xml             # inter-site WAN           10.99.0.0/24
    net-data-a.xml          # DC-A data/VIP net        10.10.1.0/24
    net-data-b.xml          # DC-B data/VIP net        10.10.2.0/24
    net-hb-a.xml            # DC-A heartbeat/repl net  172.16.1.0/24
    net-hb-b.xml            # DC-B heartbeat/repl net  172.16.2.0/24
  scripts/
    net-up.sh               # define/start/autostart all networks (idempotent)
    net-down.sh             # destroy/undefine all networks
    smoke-test.sh           # per-node connectivity matrix + severability
docs/reports/
  2026-06-XX-phase-a-provider-spike.md   # spike findings (Task 4)
```

IP convention: site-A nodes take host octets `.11/.12/.13`, site-B nodes
`.21/.22/.23`, on every network they attach to. The vagrant-libvirt
management network (DHCP, SSH) is separate and untouched.

All `vagrant`/`virsh` state stays under `lab/` and libvirt's own storage —
nothing in `build/` is required by this phase; `.vagrant/` is already
gitignored.

---

### Task 1: Spike — libvirt + KVM sanity

**Files:** none (verification only).

- [ ] **Step 1: Validate the virtualization host**

```bash
virt-host-validate qemu 2>&1 | head -8
virsh -c qemu:///system net-list --all
```

Expected: `/dev/kvm` checks PASS (warnings about cgroup controllers are
acceptable); the default libvirt network listed. If `virt-host-validate` is
missing, it ships in `libvirt-clients`; if KVM rows FAIL, the entry gate was
not actually green — stop.

- [ ] **Step 2: Confirm the libvirt storage pool**

```bash
virsh -c qemu:///system pool-list --all
```

Expected: a `default` pool (dir, `/var/lib/libvirt/images`), state active or
inactive. If absent, create it:

```bash
virsh -c qemu:///system pool-define-as default dir --target /var/lib/libvirt/images
virsh -c qemu:///system pool-start default
virsh -c qemu:///system pool-autostart default
```

Guest disks deliberately live on the VM's own disk, not the host mount
(spec §7.4 — avoid 9p/virtiofs under nested block I/O).

### Task 2: Spike — arm64 guest boots KVM-accelerated

**Files:**
- Create: `lab/spike/Vagrantfile` (temporary — deleted in Task 8)

- [ ] **Step 1: Write the single-machine spike Vagrantfile**

```ruby
# lab/spike/Vagrantfile — Phase A provider spike (temporary).
Vagrant.configure("2") do |config|
  config.vm.define "spike-arm64" do |m|
    m.vm.box = "cloud-image/ubuntu-24.04"   # candidate 1; see Step 2
    m.vm.provider :libvirt do |lv|
      lv.driver = "kvm"
      lv.cpus   = 1
      lv.memory = 1024
    end
  end
end
```

- [ ] **Step 2: Boot it, falling through box candidates**

```bash
cd lab/spike && vagrant up spike-arm64 --provider=libvirt
```

Expected: box downloads, guest boots, `vagrant ssh spike-arm64 -c 'uname -m'`
prints `aarch64`. Box candidates in order — stop at the first that publishes
a libvirt-provider arm64 build: `cloud-image/ubuntu-24.04`,
`bento/ubuntu-24.04`, `alvistack/ubuntu-24.04`. **Checkpoint:** if none
provides arm64+libvirt, stop and record it in the spike report — the
resolution (building a box from the Ubuntu cloud qcow2, which needs
`guestfs-tools` added to `vergil.toml`) is a deliberate decision, not an
improvisation.

- [ ] **Step 3: Prove it is KVM, not TCG**

```bash
virsh -c qemu:///system dominfo lab-spike_spike-arm64 | grep -i 'cpu\|virt'
vagrant ssh spike-arm64 -c 'systemd-detect-virt'
```

Expected: domain type `kvm` (not `qemu`); guest reports `kvm`. This is the
spec §7.2 leading-hypothesis confirmation. Record boot wall-clock in the
spike report.

### Task 3: Spike — x86-64 guest boots under TCG

**Files:**
- Modify: `lab/spike/Vagrantfile`

- [ ] **Step 1: Add the emulated machine**

```ruby
  config.vm.define "spike-x86", autostart: false do |m|
    m.vm.box = "almalinux/9"                # x86_64 stand-in; RHEL is Phase C
    m.vm.provider :libvirt do |lv|
      lv.driver       = "qemu"              # TCG — no KVM for cross-arch
      lv.arch         = "x86_64"
      lv.machine_type = "q35"
      lv.cpus         = 2
      lv.memory       = 2048
    end
    m.vm.boot_timeout = 1800                # TCG boots are minutes, not seconds
  end
```

- [ ] **Step 2: Boot and verify**

```bash
cd lab/spike && time vagrant up spike-x86 --provider=libvirt
vagrant ssh spike-x86 -c 'uname -m && systemd-detect-virt'
```

Expected: `x86_64` and `qemu` (TCG). Slow is fine; record the wall-clock.
**Checkpoint:** if boot exceeds ~30 min or wedges, that is the spec §6
break-glass signal — record it and surface the cloud-x86 split decision to
the human; do not tune timers to mask it. (RHEL-proper licensing/box
acquisition is deliberately Phase C, per spec §10-C; AlmaLinux only proves
the cross-arch mechanics here.)

### Task 4: Spike — severable multi-NIC networking + report

**Files:**
- Create: `lab/networks/net-hb-a.xml` (first of the set; rest in Task 5)
- Create: `docs/reports/2026-06-XX-phase-a-provider-spike.md`
- Modify: `lab/spike/Vagrantfile`

- [ ] **Step 1: Define one isolated heartbeat network**

```xml
<!-- lab/networks/net-hb-a.xml — DC-A private heartbeat/replication net.
     No <forward> element: isolated, host-only, no DHCP - exactly what a
     severable cluster heartbeat needs. -->
<network>
  <name>net-hb-a</name>
  <bridge name="virbr-hb-a"/>
  <ip address="172.16.1.1" netmask="255.255.255.0"/>
</network>
```

```bash
virsh -c qemu:///system net-define lab/networks/net-hb-a.xml
virsh -c qemu:///system net-start net-hb-a
```

- [ ] **Step 2: Attach the arm64 spike guest to it**

In `lab/spike/Vagrantfile`, inside the `spike-arm64` define block:

```ruby
    m.vm.network :private_network,
      :libvirt__network_name => "net-hb-a",
      :ip => "172.16.1.11",
      :libvirt__dhcp_enabled => false
```

```bash
vagrant reload spike-arm64
vagrant ssh spike-arm64 -c 'ip -br addr'
```

Expected: a second NIC with `172.16.1.11`.

- [ ] **Step 3: Sever and restore the link (the §3.1 fault primitive)**

```bash
DOM=lab-spike_spike-arm64
IF=$(virsh -c qemu:///system domiflist "$DOM" | awk '/net-hb-a/{print $1}')
virsh -c qemu:///system domif-setlink "$DOM" "$IF" down
vagrant ssh spike-arm64 -c 'ip -br link'      # expect NO-CARRIER on that NIC
virsh -c qemu:///system domif-setlink "$DOM" "$IF" up
vagrant ssh spike-arm64 -c 'ip -br link'      # expect carrier restored
```

Expected: link state visible from inside the guest, reversibly. Also verify
whole-network severance: `virsh net-destroy net-hb-a` then `net-start` — this
is the per-net (vs per-NIC) fault axis.

- [ ] **Step 4: Write the spike report and commit**

Create `docs/reports/2026-06-XX-phase-a-provider-spike.md` (date it the day
it runs) recording, per §7.2: KVM-arm64 confirmed (boot time), TCG-x86
confirmed (boot time, usability verdict), severability confirmed (both
axes), box candidates that worked, and any fallback decision triggered.
Then:

```bash
vrg-git add lab/ docs/reports/
vrg-commit --type feat --scope lab \
  --message "provider spike: nested vagrant-libvirt validated (#15)" \
  --body "KVM arm64 + TCG x86-64 + severable multi-NIC networks per spec 7.2. Findings in the spike report. Ref #15"
```

### Task 5: Network fabric — the full topology net set

**Files:**
- Create: `lab/networks/net-{svc,client,wan,data-a,data-b,hb-b}.xml`
- Create: `lab/scripts/net-up.sh`, `lab/scripts/net-down.sh`

- [ ] **Step 1: Write the six remaining network XMLs**

Same shape as `net-hb-a.xml` (isolated: no `<forward>`, no DHCP), one file
each:

| file | name | bridge | ip/netmask |
|---|---|---|---|
| net-svc.xml | net-svc | virbr-svc | 10.20.0.1 / 255.255.255.0 |
| net-client.xml | net-client | virbr-client | 10.30.0.1 / 255.255.255.0 |
| net-wan.xml | net-wan | virbr-wan | 10.99.0.1 / 255.255.255.0 |
| net-data-a.xml | net-data-a | virbr-data-a | 10.10.1.1 / 255.255.255.0 |
| net-data-b.xml | net-data-b | virbr-data-b | 10.10.2.1 / 255.255.255.0 |
| net-hb-b.xml | net-hb-b | virbr-hb-b | 172.16.2.1 / 255.255.255.0 |

- [ ] **Step 2: Write the idempotent up/down scripts**

```bash
#!/usr/bin/env bash
# lab/scripts/net-up.sh — define, start, autostart every lab network.
set -euo pipefail
cd "$(dirname "$0")/../networks"
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-info "$net" >/dev/null 2>&1 \
    || virsh -c qemu:///system net-define "$xml"
  virsh -c qemu:///system net-info "$net" | grep -q 'Active:.*yes' \
    || virsh -c qemu:///system net-start "$net"
  virsh -c qemu:///system net-autostart "$net" >/dev/null
  echo "up: $net"
done
```

```bash
#!/usr/bin/env bash
# lab/scripts/net-down.sh — tear down every lab network (guests first!).
set -euo pipefail
cd "$(dirname "$0")/../networks"
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-destroy  "$net" 2>/dev/null || true
  virsh -c qemu:///system net-undefine "$net" 2>/dev/null || true
  echo "down: $net"
done
```

- [ ] **Step 3: Run up, verify, run down, run up again (idempotence)**

```bash
chmod +x lab/scripts/net-*.sh
lab/scripts/net-up.sh && virsh -c qemu:///system net-list
lab/scripts/net-up.sh                      # second run: no errors, no dupes
```

Expected: all seven networks active+autostart; re-run is a no-op.

- [ ] **Step 4: Commit**

```bash
vrg-git add lab/networks lab/scripts
vrg-commit --type feat --scope lab \
  --message "multi-site network fabric: seven severable libvirt nets (#15)" \
  --body "Per spec section 5: SVC, client, WAN, per-DC data and heartbeat nets. Isolated (no forward, no DHCP), idempotent net-up/net-down scripts. Ref #15"
```

### Task 6: Topology config + parameterized Vagrantfile

**Files:**
- Create: `lab/topology.yaml`
- Create: `lab/Vagrantfile`

- [ ] **Step 1: Write the declarative topology**

```yaml
# lab/topology.yaml — single source of truth for the lab shape.
# Phase A: 3+3 placeholder nodes, Ubuntu arm64 (KVM). The RDQM arm swaps
# platform to rhel9-x86_64 per-node in Phase C without touching the
# Vagrantfile.
boxes:
  ubuntu2404-arm64: { box: cloud-image/ubuntu-24.04, arch: aarch64, driver: kvm }
  alma9-x86_64:     { box: almalinux/9,               arch: x86_64,  driver: qemu }

defaults: { platform: ubuntu2404-arm64, cpus: 1, memory: 1024 }

nodes:
  node-a1:
    nics: { net-data-a: 10.10.1.11, net-hb-a: 172.16.1.11,
            net-wan: 10.99.0.11, net-svc: 10.20.0.11 }
  node-a2:
    nics: { net-data-a: 10.10.1.12, net-hb-a: 172.16.1.12,
            net-wan: 10.99.0.12, net-svc: 10.20.0.12 }
  node-a3:
    nics: { net-data-a: 10.10.1.13, net-hb-a: 172.16.1.13,
            net-wan: 10.99.0.13, net-svc: 10.20.0.13 }
  node-b1:
    nics: { net-data-b: 10.10.2.21, net-hb-b: 172.16.2.21,
            net-wan: 10.99.0.21, net-svc: 10.20.0.21 }
  node-b2:
    nics: { net-data-b: 10.10.2.22, net-hb-b: 172.16.2.22,
            net-wan: 10.99.0.22, net-svc: 10.20.0.22 }
  node-b3:
    nics: { net-data-b: 10.10.2.23, net-hb-b: 172.16.2.23,
            net-wan: 10.99.0.23, net-svc: 10.20.0.23 }
```

(`net-client` carries fixtures/containers in Phase B; no Phase A nodes.)

- [ ] **Step 2: Write the Vagrantfile that renders it**

```ruby
# lab/Vagrantfile — multi-machine lab, driven entirely by topology.yaml.
require "yaml"

topology = YAML.load_file(File.join(__dir__, "topology.yaml"))
boxes    = topology.fetch("boxes")
defaults = topology.fetch("defaults")

Vagrant.configure("2") do |config|
  topology.fetch("nodes").each do |name, spec|
    spec ||= {}
    platform = boxes.fetch(spec.fetch("platform", defaults["platform"]))
    config.vm.define name do |node|
      node.vm.box      = platform.fetch("box")
      node.vm.hostname = name
      node.vm.provider :libvirt do |lv|
        lv.driver = platform.fetch("driver")
        if platform["arch"] == "x86_64"
          lv.arch         = "x86_64"
          lv.machine_type = "q35"
          node.vm.boot_timeout = 1800
        end
        lv.cpus   = spec.fetch("cpus",   defaults["cpus"])
        lv.memory = spec.fetch("memory", defaults["memory"])
      end
      spec.fetch("nics", {}).each do |net, ip|
        node.vm.network :private_network,
          :libvirt__network_name => net,
          :ip => ip,
          :libvirt__dhcp_enabled => false
      end
    end
  end
end
```

- [ ] **Step 3: Validate without booting**

```bash
cd lab && vagrant validate && vagrant status
```

Expected: `Vagrantfile validated successfully`; six machines listed
`not created`.

- [ ] **Step 4: Commit**

```bash
vrg-git add lab/topology.yaml lab/Vagrantfile
vrg-commit --type feat --scope lab \
  --message "declarative topology + parameterized multi-machine Vagrantfile (#15)" \
  --body "3+3 node shape from spec section 5; platform/{os,arch} swappable per node via topology.yaml per spec section 6. Ref #15"
```

### Task 7: Boot the 3+3 skeleton + smoke test

**Files:**
- Create: `lab/scripts/smoke-test.sh`

- [ ] **Step 1: Bring up the fabric and all six nodes**

```bash
lab/scripts/net-up.sh
cd lab && vagrant up --provider=libvirt
vagrant status
```

Expected: six machines `running`. (~6 GB guest RAM total — well inside the
62 GB budget.)

- [ ] **Step 2: Write the connectivity-matrix smoke test**

```bash
#!/usr/bin/env bash
# lab/scripts/smoke-test.sh — every node must reach its OWN site's nets and
# the WAN; it must NOT reach the other site's private nets. Exits non-zero
# on any violation.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
check() { # node target expect(0|1)
  if vagrant ssh "$1" -c "ping -c1 -W2 $2" >/dev/null 2>&1; then got=0; else got=1; fi
  if [ "$got" -ne "$3" ]; then echo "FAIL: $1 -> $2 (expect $3, got $got)"; fail=1
  else echo "ok:   $1 -> $2"; fi
}
for i in 1 2 3; do
  check "node-a$i" 10.10.1.1   0   # own data net gateway
  check "node-a$i" 172.16.1.1  0   # own heartbeat net
  check "node-a$i" 10.99.0.2$i 0   # peer site over WAN
  check "node-a$i" 172.16.2.1  1   # other site's heartbeat: unreachable
  check "node-b$i" 10.10.2.1   0
  check "node-b$i" 172.16.2.1  0
  check "node-b$i" 10.99.0.1$i 0
  check "node-b$i" 172.16.1.1  1
done
exit "$fail"
```

- [ ] **Step 3: Run it**

```bash
chmod +x lab/scripts/smoke-test.sh && lab/scripts/smoke-test.sh
```

Expected: all `ok`, exit 0. The two `expect 1` rows per node prove the
site-private nets are genuinely isolated — the property the §3.1 fault suite
depends on.

- [ ] **Step 4: Severability drill on a live cluster node**

```bash
DOM=lab_node-a1
IF=$(virsh -c qemu:///system domiflist "$DOM" | awk '/net-hb-a/{print $1}')
virsh -c qemu:///system domif-setlink "$DOM" "$IF" down
vagrant ssh node-a1 -c 'ip -br link | grep -i no-carrier'
virsh -c qemu:///system domif-setlink "$DOM" "$IF" up
lab/scripts/smoke-test.sh            # matrix green again
```

Expected: carrier drops and restores; smoke test passes after restore.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/scripts/smoke-test.sh
vrg-commit --type feat --scope lab \
  --message "3+3 skeleton boots; connectivity-matrix smoke test (#15)" \
  --body "Six nodes across two simulated DCs; per-site isolation and WAN reachability asserted; per-NIC severability drilled. Ref #15"
```

### Task 8: Reproducibility proof + spike cleanup

**Files:**
- Delete: `lab/spike/` (the temporary spike Vagrantfile)

- [ ] **Step 1: Tear down the spike guests and directory**

```bash
cd lab/spike && vagrant destroy -f && cd .. && rm -rf spike/
```

- [ ] **Step 2: Full teardown / rebuild cycle**

```bash
cd lab && vagrant destroy -f
lab/scripts/net-down.sh
lab/scripts/net-up.sh
vagrant up --provider=libvirt
lab/scripts/smoke-test.sh
```

Expected: clean rebuild from the committed definitions alone, smoke test
green — the "disposable per-run harness, durable model" property (spec §0).

- [ ] **Step 3: Validate and commit**

```bash
vrg-container-run -- vrg-validate     # green before the commit, not after
vrg-git add -A lab/
vrg-commit --type feat --scope lab \
  --message "harness reproducibility proven; spike scaffolding removed (#15)" \
  --body "Full destroy/rebuild cycle from committed definitions with green smoke test. Phase A complete per spec section 10-A. Ref #15"
```

---

## Deliberately deferred (recorded so they are decisions, not omissions)

- **Guest service-surface minimization** (spec §7.2 "worth a day") — first
  matters when real MQ cluster nodes run under emulation; lands with the
  Ansible L0 layer in Phase B/C, reusing the `vergil-vm` masking precedent.
- **WAN latency injection** (`tc netem` on `virbr-wan`) — needed for the DR
  window measurements in Phase C, not for the harness skeleton.
- **RHEL-proper x86 box** — licensing + box acquisition is Phase C's first
  task; AlmaLinux proves the cross-arch mechanics here (spec §6 scope note).
- **Pacemaker-arm extras** (witness + iSCSI target VM) — Phase D shape,
  added as `topology.yaml` nodes when that arm starts.

## Self-review notes

- **Spec coverage:** §10-A asks for a multi-site, multi-network,
  {os,arch}-parameterized Vagrant harness entered via the §7.2 spike. Tasks
  1–4 are the spike (KVM-arm64, TCG-x86, severability, report = §7.2's
  required evidence); Task 5 is §5's network list; Task 6 is the {os,arch}
  parameterization (§6); Task 7 proves the 3+3 shape and the §3.1 fault
  primitive; Task 8 proves §0's reproducibility claim.
- **Honest unknowns are checkpoints, not placeholders:** box availability
  (Task 2 Step 2) and TCG viability (Task 3 Step 2) stop and surface rather
  than improvise — both have named fallbacks from the spec (§6 break-glass,
  §7.2 fallback).
- **Naming consistency:** networks `net-*` match between XML files,
  `topology.yaml`, smoke test, and severability drills; libvirt domain names
  are `<project-dir>_<machine>` (`lab_node-a1`) — verify the prefix on first
  `vagrant up` and adjust the drill commands if the directory name differs.
- **Workflow:** every task ends in a `vrg-commit` on this issue's feature
  branch; repo validation (`vrg-container-run -- vrg-validate`) runs before
  the final commit and before the PR template is written.
