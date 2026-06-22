# Native HA on RHEL — Phase 3: CRR / full HADR (the keystone)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps
> use `- [ ]`. Code (arm verbs, `mqlab dr` wiring) is TDD via `vrg-validate`; the
> Ansible/lab steps are run-observe-record checkpoints, fail-loud. Builds on the
> proven Phase-0 CRR spike + Phase-1 HA arm.

**Goal:** Complete the `distributed-nativeha-rhel` HADR keystone (#267): add the
site-B Recovery group + Cross-Region Replication so a **controlled cutover moves
live distributed messaging (app ↔ QMNATIVE ↔ QMSVC) across sites**, with TLS on
the CRR link and the `mqlab dr` `cutover`/`failback` verbs driving it.

**Architecture:** A second 3-node Native HA group on `nha_rhel_b` (site B) pairs
with the site-A Live group via CRR (async, over `net-wan`). Roles
(Live/Recovery) swap by `qm.ini` `GroupRole` edit + restart (proven in the
spike), surfaced through the arm's `dr` verbs. TLS on the CRR link is sourced
from the built `lab-pki` provider — gated behind extracting GSKit (the spike's
tarball gotcha). The whole thing rides the **one consolidated setup**; nothing
new is registered.

**Tech stack:** RHEL 9.6 x86-64 (TCG), MQ 9.4.5 Native HA CRR, `lab-pki`,
`mqmonitor@` systemd, `mqlab dr`, Ansible. Functional correctness only.

## Global Constraints
- **CRR mechanics are proven** (Phase-0 spike): `NativeHALocalInstance` group
  fields + `NativeHARecoveryGroup`; async cross-region; manual switchover via
  `GroupRole` edit + restart; persistent messages survive. Reuse verbatim.
- **CRR is plaintext-capable** (spike) — TLS is *security*, not function. So DR
  mechanics never block on TLS.
- **TLS requires GSKit initialized first** (`lab-gotchas.md`: tarballs unextracted
  → `runmqakm` ICU failure). This is the one real unknown.
- **Site B = `nha_rhel_b`** (`nha-rhel-b1..3`), CRR over `net-wan`. No new setup.
- `mqmonitor@` lifecycle; fail loud; no numeric timing (TCG).

## Entry gate
```bash
# Phase 1 complete (site-A HA arm proven, cold-rebuild passed). lab-pki built.
ls ansible/roles/lab-pki ansible/site-pki.yml ansible/vars/pki-entities.yml
sudo ls /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso   # staged
# Native HA VMs are ours to rebuild; pcmk arm stays up (dashboard).
```

## File structure
```text
ansible/roles/mq-nativeha/tasks/gskit-RedHat.yml    # extract/init GSKit (NEW; the gotcha fix)
ansible/site-nativeha-dr.yml                         # site A + site B groups + CRR wiring (NEW)
ansible/roles/mq-nativeha/tasks/crr.yml             # NativeHARecoveryGroup + (TLS) NativeHALocalInstance (NEW)
lab/topology.yaml                                    # nativeha-rhel arm: dr verbs (modify)
src/mqlab/...                                         # native-ha dr-bootstrap/cutover/failback dispatch (modify if needed)
tests/test_topology_nativeha.py                      # assert dr verbs (modify)
lab/scripts/nativeha-dr-drills.sh                    # planned switchover + unplanned failover (NEW)
docs/reports/2026-06-18-nativeha-rhel-phase3-findings.md
```

---

### Task 1: TLS keystore path — RESOLVED & VERIFIED (2026-06-18)

**Root cause (`lab-gotchas.md`):** `runmqakm` failed only because the minimal RHEL
box lacked the **OS `libicu`** (`libicuio.so.67`). **`dnf install libicu` fixes it**
— `runmqakm` then works. (The earlier "unusable / .p12-only" readings were wrong.)

**Path (verified on site A):** install `libicu`; deploy the `lab-pki` PKCS#12
(`.p12`, OpenSSL); stash its password with `runmqakm -keydb -stashpw -type pkcs12`;
set `NativeHALocalInstance` `CipherSpec=ANY_TLS12` / `CertificateLabel=QMNATIVE` /
`KeyRepository=<.p12 stem>`. Codified in `mq-nativeha/tasks/tls.yml` +
`site-nativeha-tls.yml`.

- [x] **Native HA replication TLS accepts the lab-pki `.p12` — VERIFIED.** Site-A
  group re-formed `QUORUM(3/3) INSYNC` with replication negotiating
  `ECDHE_RSA_AES_256_GCM_SHA384`. The Task-4 risk is cleared; CRR cross-region uses
  the same mechanism. (Plaintext fallback no longer needed.)
  deliverable and escalate the ICU gap. (DR mechanics never wait on TLS.)

### Task 2: lab-pki certs for the six Native HA instances (first on RHEL)

**Files:** Modify `ansible/vars/pki-entities.yml`.

- [ ] **Step 1 — add the six `nha-rhel-{a,b}` instances** as PKI entities (mirror
  existing entries: CN, key repo path `/var/mqm/qmgrs/QMNATIVE/ssl/keystore`).
- [ ] **Step 2 — run `ansible-playbook site-pki.yml`** against `nha_rhel_a:nha_rhel_b`.
  **Checkpoint (first lab-pki-on-RHEL):** each node has its keystore; if the role
  is Ubuntu-specific, fix it (it serves all arms). Verify `runmqakm -cert -list`.

### Task 3: Site B Recovery group

- [ ] **Step 1 — bring up `nha-rhel-b1..3`** (`vagrant up --no-provision`); render
  inventory **from the worktree** (the shared-inventory gotcha, Phase-1 findings).
- [ ] **Step 2 — provision the Recovery HA group** via `site-nativeha.yml` extended
  to `nha_rhel_b` (its own `nha_site_nodes` on `net-hb-b`). Verify `QUORUM(3/3)`.

### Task 4: CRR with TLS (the cross-region link)

**Files:** Create `ansible/roles/mq-nativeha/tasks/crr.yml`, `ansible/site-nativeha-dr.yml`.

- [ ] **Step 1 — `crr.yml`** applies, per group, the `NativeHALocalInstance`
  (`GroupName`/`GroupRole`/`GroupLocalAddress=(9415)` + TLS `CipherSpec=ANY_TLS12`
  /`CertificateLabel`/`KeyRepository` from Task 2) and the `NativeHARecoveryGroup`
  (peer group's `net-wan` addresses `(9415)`, `Enabled`), on the active instance
  (raft-replicated). Reuse the spike's stanzas verbatim; addresses = `net-wan`.
- [ ] **Step 2 — `site-nativeha-dr.yml`**: site A (Live) + site B (Recovery) +
  `crr.yml` each side + restart. Run it.
- [ ] **Step 3 — verify CRR up:** `dspmq -m QMNATIVE -o nativeha -g` on both sites
  shows `GRPROLE(Live)`/`GRPROLE(Recovery)`, `CONNGRP(yes)`, `INSYNC(yes)`,
  `GRPVER(9.4.5.0)`. (If TLS blocked by Task-1 fallback: same, plaintext.)

### Task 5: `mqlab dr` verbs for the native-ha arm

**Files:** `lab/topology.yaml` (arm `dr` verbs), `tests/test_topology_nativeha.py`,
`src/mqlab/` (dispatch if needed).

- [ ] **Step 1 — failing test:** assert `nativeha-rhel` arm exposes
  `dr-cutover`/`dr-failback` verbs. Run → FAIL.
- [ ] **Step 2 — add the verbs:** the cutover = edit `GroupRole` (Live→Recovery /
  Recovery→Live) on all instances of both groups + restart (the spike's
  procedure), expressed as the arm's `dr` verbs / a `site-nativeha-switchover.yml`
  the verb invokes. `failback` is the reverse. Flip the `parity.MATRIX`
  `nativeha-rhel` DR verbs (`cutover`/`failback`/`dr-bootstrap`) toward SUPPORTED.
- [ ] **Step 3 — test PASS + `vrg-validate`.** Commit.

### Task 6: DR drills — live distributed messaging across sites (the deliverable)

**Files:** `lab/scripts/nativeha-dr-drills.sh`; findings report.

- [ ] **Step 1 — bring up the full distributed-HADR stack:** `nha_rhel_a` +
  `nha_rhel_b` + `svc` + `app` + CRR; the our-side mesh MQSC + the app's CONNAME
  list spanning **both sites'** net-data IPs (so the app follows a cutover).
- [ ] **Step 2 — planned switchover under the mesh:** put persistent trades
  (app → QMNATIVE → QMSVC); `mqlab dr cutover` (Live A → Recovery B); confirm
  Live now runs at site B, **the trades' state survived** (queue depths / a drain
  on the promoted side), and the app — reconnect-aware (`dr_mqi.py` if the
  happy-path client can't ride it; see Phase-1 finding) — resumes against site B.
- [ ] **Step 3 — `mqlab dr failback`** (B → A); confirm symmetric. Record RPO
  (0 within group, async window across) qualitatively.
- [ ] **Step 4 — unplanned site loss:** power off all of site A while Live; promote
  site B (`Enabled=false` on the recovery stanza per the spike's unplanned path);
  reconcile the async-window message delta (§4.3 of the distributed design).
  Findings table per drill.

### Task 7: Cold-rebuild gate (3+3) + finalize
- [ ] Cold rebuild **both** groups → CRR re-forms one-pass (`dspmq … -g`
  `CONNGRP(yes)`). Finalize `docs/reports/…-phase3-findings.md` (the apples-to-apples
  HADR ledger vs RDQM-DR / pcmk-DR), `vrg-validate`, finishing-a-development-branch.

---

## Deliberately deferred
- **Phase 2 (Ubuntu `nativeha-ubuntu`)** — **deprioritized** (optional/nice-to-have;
  RHEL is the deliverable). Revisit to prove RHEL-vs-Ubuntu functional equivalence.
- **Full app reconnect/rebuild contract** (`dr_mqi.py`, Plan 4) — used in the DR
  drills; its own hardening effort is separate.
- **Security posture** beyond transport TLS (mTLS client-auth, CHLAUTH/SSLPEER) —
  the requirements-driven security workstream, not this arm.

## Self-review notes
- **Spec coverage:** completes #246 §4.2/§5 Phase 3 (CRR/DR) + §4.4 `mqlab dr` verbs
  on the consolidated setup (#267). Builds on proven spike mechanics + Phase-1 role.
- **Unknown isolated up front:** GSKit extraction is Task 1 with an explicit
  plaintext fallback, so the critical DR mechanics are never blocked by TLS.
- **No new setup** — extends `distributed-nativeha-rhel` (the keystone), site B
  booted now (it was declared in Phase 1). Inventory rendered from the worktree
  (Phase-1 shared-inventory gotcha pre-applied).
