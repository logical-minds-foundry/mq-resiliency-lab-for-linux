# Native HA on RHEL — Phase 1: HA build (`nativeha-rhel` arm)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Code
> changes (topology parse, verb dispatch) are TDD with `vrg-validate`; the
> Ansible/lab tasks are run-observe-record checkpoints, fail-loud.

**Goal:** Promote the Phase-0 spike into a first-class **`nativeha-rhel`** arm: a
production `mq-nativeha` Ansible role, the arm + setups registered in
`topology.yaml`, the `mqmonitor@` systemd lifecycle verbs wired into `mqlab qm`,
`QMNATIVE` slotted into the distributed mesh, the §3.1 fault suite green, proven
by a **cold rebuild**. **HA only — CRR/DR is Phase 3.**

**Architecture:** Three RHEL 9.6 x86-64 guests (`nha-rhel-a1..3`, group `nha_rhel_a`) form
one Native HA group (raft quorum, plaintext replication). The role is split into
shared formation tasks + an OS-adapter (`install-RedHat.yml`) so Phase 2 can add
`install-Debian.yml` for Ubuntu with the formation logic unchanged. The arm is a
declarative `topology.yaml` entry whose verbs dispatch to `systemctl … mqmonitor@`.

**Tech stack:** RHEL 9.6 (existing `rhel/9.6-x86_64` box), IBM MQ Advanced for
Developers 9.4.5.0 (base packages only), Native HA (raft), `mqmonitor.py` under
systemd, vagrant-libvirt (TCG x86), Ansible, the `mqlab` CLI.

## Global Constraints

- **MQ ≥ 9.4.4 CD** (lab 9.4.5); assert before formation.
- **All three instances same arch (x86-64).** TCG-emulated — **functional
  correctness only**, qualitative RTO, no numeric timing vs the arm64 arms.
- **Lifecycle = `mqmonitor@<qm>` systemd units — never `endmqm`.**
- **Replication plaintext** (HA; TLS is Phase 3). No GSKit needed here — but the
  GSKit-tarball gotcha (`docs/reference/lab-gotchas.md`) is pre-flagged for Phase 3.
- **Security posture deferred** per the authoritative design §1 (plaintext channels).
- **Fail loud:** capture `AMQ*` verbatim; never `2>/dev/null` over an assertion.

---

## Entry gate

```bash
# Phase-0 verdict is GO (docs/reports/2026-06-18-nativeha-crr-entitlement-spike.md).
ls build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz
vagrant box list | grep 'rhel/9.6-x86_64'
# DVD ISO staged in the pool (Phase-0 lesson) — re-stage if absent:
sudo ls /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso || \
  sudo cp build/rhel-9.6-x86_64-dvd.iso /var/lib/libvirt/images/ && \
  sudo chown libvirt-qemu:kvm /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso
# CPU budget: pcmk arm64 stack stays up (dashboard dev). Tear down the Phase-0
# throwaway rdqm-a*/rdqm-b* spike nodes before this build.
```

## File structure

```text
lab/topology.yaml                                   # + nativeha-rhel arm, nha-rhel-a1..3 nodes, nha_rhel_a group, 2 setups (modify)
ansible/roles/mq-nativeha/tasks/main.yml            # shared: assert level, form group, mqmonitor@ (NEW, productionized)
ansible/roles/mq-nativeha/tasks/install-RedHat.yml  # OS-adapter: base MQ from the DVD repo + tar (NEW)
ansible/site-nativeha.yml                           # the nativeha-rhel provisioning playbook (NEW)
src/mqlab/setups.py                                 # QmConfig already carries name/vip; confirm nativeha fields (modify if needed)
tests/test_topology_nativeha.py                     # parse the new arm/setups (NEW)
lab/scripts/nativeha-fault-suite.sh                 # §3.1 steps against the group (NEW)
docs/reports/2026-06-18-nativeha-rhel-phase1-findings.md   # grows per drill (NEW)
```

IP plan (site-A `nha-*` on the data net `.5x`, replication on the hb net):

| node | data | replication (hb) |
|---|---|---|
| nha-rhel-a1..a3 | 10.50.2.51–53 | 172.16.3.51–53 |

(Replication on a dedicated NIC — unlike the spike's single-net shortcut — so the
fault suite can sever it independently.)

---

### Task 1: Topology — `nativeha-rhel` arm, nodes, setups

**Files:** Modify `lab/topology.yaml`. Test: `tests/test_topology_nativeha.py`.

- [ ] **Step 1 — write the failing parse test.** Assert the registry exposes the
  new arm and setups:

```python
# tests/test_topology_nativeha.py
import yaml, pathlib
def test_nativeha_rhel_arm_and_setups():
    t = yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())
    arm = t["arms"]["nativeha-rhel"]
    assert arm["mechanism"] == "native-ha"
    # verbs use the mqmonitor@ systemd lifecycle, not endmqm/strmqm/pcs
    assert "mqmonitor@" in arm["verbs"]["qm-up"]["cmd"]
    assert "mqmonitor@" in arm["verbs"]["qm-down"]["cmd"]
    assert t["setups"]["nativeha_ha"]["arm"] == "nativeha-rhel"
    assert t["setups"]["distributed-nativeha-rhel"]["arm"] == "nativeha-rhel"
    assert set(t["groups"]["nha_rhel_a"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}
```

- [ ] **Step 2 — run it, expect FAIL** (`KeyError: 'nativeha-rhel'`):
  `vrg-container-run -- uv run pytest tests/test_topology_nativeha.py -q`

- [ ] **Step 3 — add the topology.** Nodes `nha-rhel-a1..a3` (platform
  `rhel96-x86_64`, `cpus: 2, memory: 2048`, data + hb NICs per the IP plan — **no
  `extra_disk`**, Native HA is shared-nothing/log-based); group `nha_rhel_a`; arm:

```yaml
arms:
  nativeha-rhel:
    mechanism: native-ha
    cluster_group: nha_rhel_a
    verbs:
      qm-create:  { playbook: site-nativeha.yml }
      qm-status:  { cmd: "su - mqm -c '/opt/mqm/bin/dspmq -m {qm} -o nativeha -x'" }
      qm-up:      { cmd: "systemctl start mqmonitor@{qm}" }
      qm-down:    { cmd: "systemctl stop mqmonitor@{qm}" }   # NOT endmqm (systemd restarts it)
setups:
  nativeha_ha:
    description: Native HA (raft) — site-A 3-node HA group, plaintext (HA only)
    arm: nativeha-rhel
    groups: [nha_rhel_a]
    provision: ansible/site-nativeha.yml
    qm: { name: QMNATIVE }
  distributed-nativeha-rhel:
    description: Distributed MQ (Native HA arm) — app -> QMNATIVE <-> QMDTCC over net-ext
    arm: nativeha-rhel
    groups: [nha_rhel_a, dtcc, app]
    provision: ansible/site-nativeha.yml
    secrets: [mqweb_admin_password]
    qm: { name: QMNATIVE, vip: 10.50.2.50, dtcc_conn: 10.60.0.50 }
```

- [ ] **Step 4 — run the test, expect PASS.** Then `vrg-container-run --
  vrg-validate` (topology schema). Commit (`feat(nativeha): register nativeha-rhel
  arm + nha_rhel_a nodes + setups`).

### Task 2: Production `mq-nativeha` role (shared formation + RedHat adapter)

**Files:** Create `ansible/roles/mq-nativeha/tasks/main.yml`,
`tasks/install-RedHat.yml`; `ansible/site-nativeha.yml`.

- [ ] **Step 1 — OS-adapter `install-RedHat.yml`** (base MQ only; the proven
  Phase-0 install minus RDQM): mount the DVD (`/dev/sr0`) + dnf repos; copy+unpack
  the LinuxX64 tar; `mqlicense.sh -accept`; `dnf install
  MQSeries{Runtime,Server,GSKit,Java,JRE,Web,SDK,Client,Samples}*.rpm`;
  `setmqinst -i -p /opt/mqm`; mqm ulimits. (Copy verbatim from the Phase-0 spike
  role `mq-nativeha-spike/tasks/main.yml` install block — it is proven.)

- [ ] **Step 2 — shared `main.yml`** (OS-agnostic formation):
  - `include_tasks: "install-{{ ansible_os_family }}.yml"` (RedHat now; Debian in Phase 2).
  - assert `dspmqver` ≥ 9.4.4 CD (fail loud).
  - `crtmqm -lr {{ inventory_hostname }} -lf 8192 -lp 10 -ls 10 -p 1414 {{ qm_name }}`
    (creates-guard `/var/mqm/qmgrs/{{ qm_name }}`), as mqm.
  - `blockinfile` the `NativeHAInstance` peer set into `qm.ini` — **render the
    block inline from `groups[cluster_group]` hostvars** (the Phase-0 bug: never
    `lookup('file')`, which reads the controller). Replication address = each
    node's **hb** IP `(9414)`.
  - link `/opt/mqm/samp/mqmonitor@.service` → `/etc/systemd/system`; enable+start
    `mqmonitor@{{ qm_name }}` (daemon_reload).

- [ ] **Step 3 — `site-nativeha.yml`**: one play over `cluster_group` applying
  `mq-nativeha` with `qm_name: "{{ qm.name }}"` and `cluster_group: nha_rhel_a`.

- [ ] **Step 4 — bring up `nha_rhel_a` + provision.** Stage the DVD ISO if needed
  (entry gate). `vagrant up nha-rhel-a1 nha-rhel-a2 nha-rhel-a3 --no-provision`; then
  `mqlab qm create nativeha_ha` (or `ansible-playbook site-nativeha.yml --limit
  nha_rhel_a`). **Acceptance:** `mqlab qm status nativeha_ha` (the arm verb) →
  `QUORUM(3/3)`, one Active + two Replica, all `INSYNC(yes)`. Commit.

### Task 3: First automatic failover (the HA guarantee)

**Files:** Create `lab/scripts/nativeha-fault-suite.sh`; findings report.

- [ ] **Step 1 (suite step 1) — kill the active QM.** `kill -9` the active
  instance's `amqzxma0` (or power the active instance's MQ) → expect raft
  re-election; a replica becomes Active; `mqlab qm status` shows `QUORUM`
  maintained (2/3 then 3/3 on restart). Record qualitative RTO.
- [ ] **Step 2 (suite step 2) — hard power-off the active node.**
  `virsh destroy <active nha node>` → another instance takes Active; client
  reconnect against the connectivity address succeeds; power the node back on →
  it rejoins as Replica and resynchronizes. Record.
- [ ] **Step 3 (suite step 3) — sever the replication NIC** on the active
  (the `domif-setlink` hb-NIC primitive) → confirm **no split-brain** (exactly one
  Active across all three throughout — assert via `dspmq -o nativeha -x` on each),
  restore, resync.
- [ ] **Step 4 (suite step 5) — planned failover/failback** via `mqlab qm down`
  then `up` (the `mqmonitor@` stop/start verbs) → controlled move + return.
- [ ] **Step 5** — each drill appends a row (drill, expected, observed,
  intervention?, anomalies) to the findings report. Commit suite + results.

### Task 4: `QMNATIVE` in the distributed mesh

**Files:** Modify `ansible/site-nativeha.yml` (or a content play); reuse the
distributed `pymqrest`/`dtcc-sim`/`app-client` content from the existing arms.

- [ ] **Step 1 — bring up the `distributed-nativeha-rhel` setup** (`nha_rhel_a` + `dtcc`
  + `app`); `QMNATIVE` reached on its connectivity address; inter-QM channels
  `QMNATIVE ↔ QMDTCC` over `net-ext`. **Same app contract, new substrate.**
- [ ] **Step 2 — end-to-end flow:** `app-client` puts a trade → `QMNATIVE` →
  `QMDTCC`; `dtcc-sim` replies; confirm the reply returns. Persistent messages.
- [ ] **Step 3 — failover under load:** repeat Task 3 step 2 (kill active node)
  while the app flow runs; confirm the flow resumes after re-election (client
  auto-reconnect). Record. Commit.

### Task 5: Cold-rebuild acceptance gate + wrap

- [ ] **Step 1 — cold rebuild** (the acceptance gate, not lint-green):
  `vagrant destroy -f nha-rhel-a1 nha-rhel-a2 nha-rhel-a3`; re-stage ISO; `vagrant up --no-provision`;
  `mqlab qm create nativeha_ha`; confirm `QUORUM(3/3)` **one-pass, no manual
  fix-ups**. Any manual step needed → fold it into the role and repeat.
- [ ] **Step 2 — finalize** `docs/reports/2026-06-18-nativeha-rhel-phase1-findings.md`
  (fault-suite table, the apples-to-apples ledger vs RDQM/pcmk: what Native HA gave
  turnkey), `vrg-container-run -- vrg-validate`, final commit.
- [ ] **Step 3 — finishing-a-development-branch** (verify, then PR into `develop`).

---

## Deliberately deferred

- **CRR / cross-region DR** — Phase 3 (`nativeha_dr` 3+3, real TLS via `lab-pki`,
  GSKit extraction, `mqlab dr cutover/failback`). HA-only here.
- **Real TLS** — Phase 3; HA replication runs plaintext per the lab posture.
- **Ubuntu 24.04 arm** — Phase 2: a parallel **`nativeha-ubuntu`** arm with
  **platform-qualified** nodes `nha-ubuntu-a1..3` (group `nha_ubuntu_a`) — distinct
  from this arm's `nha-rhel-a*`/`nha_rhel_a` so both can coexist in `topology.yaml`
  (mirrors the existing `pcmk-a*` vs `pcmk-rhel-a*` convention). Native HA CRR
  forces **x86, not RHEL**, so this is x86 too; only `install-Debian.yml` differs —
  formation logic is shared. The RHEL-vs-Ubuntu functional-equivalence comparison
  (spec §2.1 headline) is the whole point of building both.
- **mqweb/metrics full parity** — minimal REST proof here; full content-plane
  parity when the comparison harness needs identical operator verbs.
- **Fault-suite storage severance** — n/a: Native HA is shared-nothing (itself a
  ledger finding vs the SAN/DRBD arms).

## Self-review notes

- **Spec coverage:** implements #246 spec §5 Phase 1 (HA-first on RHEL, cold-rebuild
  gate) and §4.1/§4.3/§4.4 (3-node raft, shared-role + OS-adapter seam, declarative
  arm verbs). CRR (§4.2) and Ubuntu (§4 Phase 2) explicitly deferred.
- **Phase-0 lessons pre-applied:** base-MQ-only install (proven role block reused);
  `mqmonitor@` lifecycle verbs (not `endmqm`); `blockinfile` rendered controller-side
  (not `lookup('file')`); DVD ISO staged in the pool; dedicated replication NIC so
  the fault suite can sever it; GSKit gotcha flagged for Phase 3.
- **Trust-but-verify, not placeholders:** exact `crtmqm -lr` flags and the
  `mqmonitor@`/`dspmq -o nativeha` verb spellings are proven from the Phase-0 spike;
  the `mqlab qm` verb-dispatch field names are re-checked against the #212 arm seam
  at execution.
