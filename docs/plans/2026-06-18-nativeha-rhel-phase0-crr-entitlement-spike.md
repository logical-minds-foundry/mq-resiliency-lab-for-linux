# Native HA CRR on RHEL — Phase 0: CRR-Entitlement Spike

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. This is a
> **lab spike**, not a TDD code change — most steps are run-observe-record against
> live RHEL guests, fail-loud, and the artifacts are **throwaway** (builds nothing
> permanent). The deliverable is a GO/STOP verdict + a findings report.

**Goal:** Prove that the lab's IBM **MQ 9.4.5 Advanced for Developers** entitlement
unlocks **Native HA Cross-Region Replication (CRR)** on RHEL 9.6 — the hard gate
from the #246 spec (§5 Phase 0). CRR/DR is non-negotiable; if the entitlement does
not permit CRR, the whole Native HA arm stops here.

**Architecture:** Two 3-node Native HA groups (Live + Recovery) on the existing
`rhel/9.6-x86_64` box, MQ installed from the no-charge Developers tar. HA
replication is plaintext (default); the cross-region CRR link uses TLS sourced
from the now-built `lab-pki` provider. Replication and role-switch are driven
entirely by `crtmqm -lr` + `qm.ini` stanzas + `mqmonitor@` systemd units, per
IBM's CRR-on-Linux walkthrough (cached at
`build/refs/ibm-docs/mq/9.4.x/crr-linux/configuring-crr-on-linux.pdf`).

**Tech stack:** RHEL 9.6 x86-64 (existing box, TCG), IBM MQ Advanced for
Developers 9.4.5.0 LinuxX64, Native HA (raft), CRR, `lab-pki` (TLS), Ansible.

## Global Constraints

- **MQ floor 9.4.4** for off-container Native HA/CRR (lab runs 9.4.5). Confirm
  `dspmqver` ≥ 9.4.4 before configuring CRR.
- **All six instances: same processor architecture** (x86-64) — non-negotiable
  Native HA rule.
- **Lifecycle is `mqmonitor@<qm>` systemd units — never `endmqm`** (systemd would
  restart it). Stop via `systemctl stop mqmonitor@<qm>`.
- **HA replication is plaintext by default; CRR uses TLS** (CipherSpec on
  `NativeHALocalInstance`, certs from `lab-pki`).
- **Functional correctness only** — TCG-emulated; no timing claims, qualitative
  RPO/RTO only (spec §2.3).
- **Fail loud, no masking:** never `2>/dev/null` over an assertion; capture exact
  MQ output (especially any `AMQ*` licensing message) verbatim.

---

## Entry gate

Confirm before starting (stop and surface if any fails):

```bash
# 1. Security/PKI prerequisite is met (#246 §6 fork-2): lab-pki can issue certs.
ls ansible/roles/lab-pki ansible/site-pki.yml ansible/vars/pki-entities.yml
# 2. MQ Developers media present (gitignored build/).
ls build/mq/9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz
# 3. The RHEL 9.6 box exists.
vagrant box list | grep 'rhel/9.6-x86_64'
# 4. CPU budget: only ONE arm runs at a time (pivot non-goal). Bring the
#    RDQM and pcmk arms down before standing up six nativeha guests.
virsh -c qemu:///system list --name
```

**Substrate note (autonomous, decided 2026-06-18):** for spikes/dev the agent runs
the **whole thing** autonomously — it has confirmed `vagrant` + `virsh`
(`qemu:///system`) control from the dev VM. (Human-operation is reserved for
post-merge final verification.)

**Node strategy (decided 2026-06-18): reuse the `rdqm-*` x86 RHEL slots.** The
Ubuntu/pcmk arm is **arm64** and Native HA requires one arch (x86), so the only
x86 substrate is the RHEL nodes. They are currently **`not created`**, so this
spike **brings them up `--no-provision`** (skipping the RDQM provisioner) and runs
the **base-MQ-only** `mq-nativeha-spike` role instead — Live group = `rdqm_a`
(`rdqm-a1..3`), Recovery group = `rdqm_b` (`rdqm-b1..3`). No throwaway `nha-*`
nodes are added.

**Leave the pcmk (arm64) stack UP** — it is in active use for parallel dashboard
dev. Consequence: no "one arm at a time" luxury here; the x86 TCG guests run
*alongside* it, so **stage the bring-up (Live 3 first, Recovery +3 only when
needed)** to bound CPU pressure.

## File structure

```text
lab/topology.yaml                                  # + 6 nativeha-rhel nodes + spike setup (modify, throwaway block)
ansible/roles/mq-nativeha-spike/tasks/main.yml     # minimal: install MQ, form NHA group, wire CRR (throwaway)
ansible/roles/mq-nativeha-spike/templates/qm.ini.j2
ansible/site-nativeha-spike.yml                    # the spike playbook
ansible/vars/pki-entities.yml                      # + the 6 nativeha instances as PKI entities (modify)
docs/reports/2026-06-18-nativeha-crr-entitlement-spike.md   # the verdict + evidence
```

IP plan (reuse the RDQM site layout family; these are throwaway nodes):

| node | group | data IP | replication IP |
|---|---|---|---|
| nha-a1..a3 | Live (DataCenter1) | 10.50.2.31–33 | 172.16.3.31–33 |
| nha-b1..b3 | Recovery (DataCenter2) | 10.50.2.41–43 | 172.16.3.41–43 |

(Replication addresses: HA group `(9414)`, CRR group `(9415)` per node 7261515.)

---

### Task 1: Stand up two 3-node RHEL groups + install MQ (Native HA entitled?)

**Files:** Modify `lab/topology.yaml` (throwaway `nha-*` nodes +
`nativeha_spike` setup); create `ansible/roles/mq-nativeha-spike/tasks/main.yml`,
`ansible/site-nativeha-spike.yml`.

- [ ] **Step 1: Add six throwaway nodes + a spike setup to `topology.yaml`.**
  Mirror the `rdqm-a*`/`rdqm-b*` node shape (platform `rhel96-x86_64`,
  `cpus: 2, memory: 2048`, data + replication NICs per the IP plan). Add a
  `nativeha_spike` setup grouping `[nha_a, nha_b]`. Mark the block with a
  `# THROWAWAY — Phase-0 spike (#246); remove after verdict` comment.

- [ ] **Step 2: Minimal install role.** `mq-nativeha-spike` installs MQ from the
  Developers tar — the **base packages only** (no RDQM): unpack, `mqlicense.sh
  -accept`, `dnf install MQSeries{Runtime,Server,GSKit,Java,JRE,Web,Client}*.rpm`,
  `setmqinst -i -p /opt/mqm`, mqm ulimits, `systemctl disable --now firewalld`.
  (Reuse the `rdqm-install` role's structure minus the RDQM/DRBD/kmod steps.)

- [ ] **Step 3: Provision the Live group only first.** Human:
  `vagrant up nha-a1 nha-a2 nha-a3` then
  `ansible-playbook ansible/site-nativeha-spike.yml --limit nha_a`.
  Verify on each: `dspmqver` shows **9.4.5.0** and **ReleaseType: Continuous
  Delivery (CD)** (CRR requires the CD stream).

- [ ] **Step 4: Form the 3-node Native HA group (plaintext) — proves Native HA
  itself is entitled.** On each node, per the cached walkthrough:

```bash
# instance name = node hostname, qm name = nativeha
crtmqm -lr nha-a1 -lf 8192 -lp 10 -ls 10 -p 1414 nativeha   # (-lr <thisnode> on each)
```

Then write `/var/mqm/qmgrs/nativeha/qm.ini` `NativeHAInstance` stanzas (one per
node, `ReplicationAddress=<repl-ip>(9414)`), and `strmqm nativeha` on all three.

- [ ] **Step 5: Verify quorum + leader (record).**

Run: `dspmq -m nativeha -o nativeha -x`
Expected: `QUORUM(3/3)`, one `ROLE(Active)`, two `ROLE(Replica)`, all
`INSYNC(yes)`. **If the QM refuses to start / form quorum with an entitlement
error → capture the exact `AMQ*` text and STOP (Native HA itself not entitled).**

- [ ] **Step 6: Record.** Append to the findings report: Native HA group formed
  under MQ Advanced for Developers — YES/NO, with the `dspmq` output. Commit the
  topology + role (`feat(nativeha): phase-0 spike — base install + NHA group`).

### Task 2: Configure CRR with lab-pki TLS, enable replication — THE GATE

**Files:** Modify `ansible/vars/pki-entities.yml`;
`ansible/roles/mq-nativeha-spike/templates/qm.ini.j2`.

- [ ] **Step 1: Issue TLS certs for the six instances via `lab-pki`.** Add the six
  `nha-*` instances as PKI entities in `ansible/vars/pki-entities.yml` (follow the
  existing entity entries' shape), then run `ansible-playbook ansible/site-pki.yml`.
  Verify each node has its keystore under `/var/mqm/qmgrs/nativeha/ssl/`.
  **Checkpoint:** this is the first real consumption of `lab-pki` by Native HA —
  if the role can't issue an instance keystore, stop and fix `lab-pki` (it is the
  §6 prerequisite this spike validates).

- [ ] **Step 2: Provision + form the Recovery group (nha_b).** Human:
  `vagrant up nha-b1 nha-b2 nha-b3`; `--limit nha_b`; form its 3-node group the
  same way (qm name `nativeha`, its own replication IPs).

- [ ] **Step 3: Add CRR group config + TLS to both sites' `qm.ini`** (per the
  walkthrough, p13–14). On the **Live** nodes:

```ini
NativeHALocalInstance:
  CipherSpec=ANY_TLS12
  CertificateLabel=<lab-pki label>
  KeyRepository=/var/mqm/qmgrs/nativeha/ssl/keystore
  GroupName=Live
  GroupRole=live
  GroupLocalAddress=(9415)
NativeHARecoveryGroup:
  GroupName=Recovery
  ReplicationAddress=10.50.2.41(9415),10.50.2.42(9415),10.50.2.43(9415)
  Enabled=Yes
```

On the **Recovery** nodes: same `NativeHALocalInstance` with `GroupName=Recovery`
/ `GroupRole=Recovery`, and a `NativeHARecoveryGroup` for `Live` with
`Enabled=No`. (Trust-but-verify the exact label/paths against the cached PDF at
execution.)

- [ ] **Step 4: Restart both groups and read group status — THE ENTITLEMENT
  VERDICT.**

Run (any node): `dspmq -m nativeha -o nativeha -g`
Expected (entitlement OK): two group lines — `GRPNAME(Live) GRPROLE(Live)` and
`GRPNAME(Recovery) GRPROLE(Recovery)`, with `GRPVER(9.4.4.0)` and the recovery
group `INSYNC(yes)`.
**If CRR is blocked by entitlement → capture the exact `AMQ*` message verbatim and
STOP. This is the gate.**

- [ ] **Step 5: Prove cross-region replication of a real message.** On the Live
  active instance, put a **persistent** message to a test queue (reuse the Phase-B
  `APP.SVRCONN` + pymqi pattern against the Live connectivity address). Confirm via
  `dspmq … -g` that the Recovery group's `RCOVLSN`/`SYNCTIME` advance (async catch-up).
  Record qualitative replication lag.

- [ ] **Step 6: Record the verdict.** Findings report: **CRR entitled under MQ
  Advanced for Developers — GO / STOP**, with the `dspmq -g` output and (if
  blocked) the exact licensing message. Commit.

### Task 3: Confirm the DR operational shape (planned switchover)

- [ ] **Step 1: Planned switchover** (walkthrough p17): edit `GroupRole` in
  `qm.ini` on all instances (Live→Recovery, Recovery→Live), set both
  `NativeHARecoveryGroup` stanzas `Enabled=Yes`, `systemctl restart
  mqmonitor@nativeha` on all six.
- [ ] **Step 2: Verify the transition.** `dspmq … -x` shows `Pending recovery` /
  `Pending live` then settles: former Recovery is now `GRPROLE(Live)`, former Live
  is `GRPROLE(Recovery)`, replication still active, both groups `QUORUM(3/3)`.
  Confirm the test message survived the swap. Record — this is the operational
  shape the future `mqlab dr cutover/failback` verbs will wrap.

### Task 4: Verdict, findings report, teardown

- [ ] **Step 1: Finalize** `docs/reports/2026-06-18-nativeha-crr-entitlement-spike.md`:
  the GO/STOP verdict; whether CRR ran under the dev entitlement; whether `lab-pki`
  issued the instance certs cleanly; the #208 residual items closed (replication
  ports 9414/9415 reachable across the simulated WAN; firewall holes needed); any
  surprises to pre-apply forward into the Phase-1/3 build plans.
- [ ] **Step 2: Tear down the throwaway nodes** (`vagrant destroy -f nha-*`) and
  **revert the throwaway `topology.yaml` block** — the spike builds nothing
  permanent. The `mq-nativeha-spike` role's reusable parts inform (do not become)
  the real `mq-nativeha` role.
- [ ] **Step 3: `vrg-container-run -- vrg-validate`** (for the topology/vars edits),
  final commit, and **report the verdict to the human** as the gate to writing the
  Phase-1 (HA build) and Phase-3 (CRR/DR build) plans.

---

## What comes after (not part of this plan)

This spike is the gate. On **GO**, two follow-on plans get written, each
applying this spike's lessons:

- **Phase 1 — `nativeha-rhel` HA build:** the production `mq-nativeha` role +
  RedHat OS-adapter, `nativeha-rhel` arm + `nativeha_ha`/`distributed-nativeha-rhel`
  setups in `topology.yaml`, the `mqmonitor@` `qm-up`/`qm-down`/`qm-status` verbs,
  `QMNATIVE` slotted into the mesh, the §3.1 fault suite, **cold-rebuild gate**.
- **Phase 3 — CRR/DR on `nativeha-rhel`:** `nativeha_dr` (3+3) consuming `lab-pki`,
  the `mqlab dr` `cutover`/`failback` wiring, DR drills.

Then **Phase 2** parameterizes the proven RHEL role to **Ubuntu 24.04** (its own
plan), testing the §4.3 OS-transparency hypothesis.

## Self-review notes

- **Spec coverage:** implements spec §5 Phase 0 in full (the hard CRR-entitlement
  gate) and exercises the §4.2 `lab-pki` consumption path early. Phases 1/3/2 are
  explicitly deferred to their own plans (Scope Check) — each produces working,
  testable software on its own.
- **No placeholders:** every lab step has a run command + expected output. The one
  unknown I do **not** invent is the exact entitlement-rejection `AMQ*` code — the
  plan instruments to capture it verbatim rather than asserting it (trust-but-verify).
- **Lessons pre-applied:** single MQ rpm pass before any QM; `mqmonitor@` not
  `endmqm`; persistent messages in replication tests; fail-loud on any `AMQ*`;
  throwaway nodes + reverted topology (builds-nothing-permanent spike, per the
  #187 guardrail spirit).
- **Prerequisite honored:** runs only after the security/PKI layer is complete
  (#246 §6 fork-2), and its Task 2 is the first proof that `lab-pki` serves Native HA.
