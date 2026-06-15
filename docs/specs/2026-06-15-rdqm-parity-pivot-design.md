# RDQM Parity Pivot — Design

> **Status:** design, ready for planning.
> **Date:** 2026-06-15
> **Author:** Phillip Moore (with Claude)
> **Supersedes:** the two-arm comparison framing in
> [`2026-06-03-mq-cluster-lab-design.md`](2026-06-03-mq-cluster-lab-design.md)
> §0–§2 / §10. See that doc's "Decision & Pivot (2026-06-15)" section for the
> authoritative banner; this spec is the transition design it points to.
> **Tracking issue:** #187.

---

## 1. Why this exists

The platform decision is made: the firm standardizes on **RHEL + RDQM**, for
**IBM-supportability** reasons — the criterion the authoritative design already
weighted heaviest (§2.7, §3). The R&D comparison did its job and pointed where
the firm independently landed.

But the lab's history left RDQM in an awkward place. The **RDQM substrate was
built and proven first** (PR #36 / issue #31): a RHEL 9.6 kickstart box, the
`rdqm-install` and `rdqm-ha` roles, a live `QMRDQM` on a floating VIP, the §3.1
fault suite at 4/4, and the full 3+3 `rdqmdr` cutover/failback with RPO 0 in both
directions. Then it **froze**. Every piece of tooling sophistication since — the
`mqlab` orchestrator CLI, the net/vm lifecycle, the observability stack
(Grafana/Prometheus/`mq_prometheus`), the distributed QM-to-QM architecture
(`QMPCMK` ↔ `QMDTCC`), declarative `pymqrest` content, the MQ Service panel —
grew against the **Pacemaker/Ubuntu** arm. #169 even explicitly "set RDQM-DR
aside" and scoped the new `mqlab dr`/`ha` command groups to Pacemaker only.

So RDQM is **proven but primitive**, and the real RHEL work is **not resuming an
unfinished build** — it is **bringing the matured tooling onto the proven RDQM
substrate** and making RDQM the fully-tooled, primary arm.

## 2. Goals & non-goals

### Goals

- **G1 — RDQM at full parity** with what the Pacemaker arm demonstrates today:
  the distributed architecture with **`QMRDQM` as the in-house HA substrate**
  (`QMRDQM` ↔ `QMDTCC` across the simulated WAN), the app trade flow,
  observability, declarative `pymqrest` content, and the §3.1 fault + DR drills —
  all on RHEL.
- **G2 — Parity as a standing property, not a snapshot.** Both arms are
  co-maintained and co-testable going forward; the Phase-E comparison becomes a
  re-assertable result, not a one-shot writeup.
- **G3 — An open, N-arm abstraction.** "Arm" is a first-class registry across
  independent axes (HA mechanism × OS platform × substrate), not a hardcoded
  `rdqm | pcmk` binary — so the two future arms fold in without a retrofit.

### Non-goals (this pivot)

- **No build work on the `nativeha-rhel` or `pcmk-debian` slots.** They are
  *declarative slots* only here (§6). Each gets its own spec→plan→build later,
  gated on the framework being proven on the `rdqm-rhel` + `pcmk-ubuntu` pair.
- **No performance/timing claims for RDQM.** Per §6 of the authoritative design,
  the RDQM arm is TCG-emulated x86 — **functional correctness only**; RTO
  wall-clock is reported qualitatively and never numerically compared to the
  arm64 Pacemaker arm.
- **No security hardening** — out of scope per §1 of the authoritative design,
  unchanged here.
- **No retirement of Ubuntu/Pacemaker.** It stays first-class.

## 3. The arm abstraction (approach A — the structural seam)

One operator surface, pluggable backends. The seam falls exactly where the
authoritative design already located divergence: §6 ("confined to the L0
host-prep layer") and §3.1 (the fault suite "runs identically against each arm").

### 3.1 Two pluggable axes + a substrate axis → an arm registry

An **arm** is a named point declaring three things:

- **HA/DR mechanism backend** — `rdqm` · `pacemaker-san` · (later) `native-ha`
- **OS host-prep platform (L0)** — `rhel` · `ubuntu` · (later) `debian`
- **Substrate model** — `vm` · (later) `container/k8s`

The registry names the *valid* combinations (not a full cross-product):

| Arm | Mechanism | OS | Substrate | Status |
|---|---|---|---|---|
| `pcmk-ubuntu` | `pacemaker-san` | `ubuntu` | `vm` | built (reference backend) |
| `rdqm-rhel` | `rdqm` | `rhel` | `vm` | **priority — this pivot** |
| `nativeha-rhel` | `native-ha` | `rhel` | `container/k8s` | slot only (§6) |
| `pcmk-debian` | `pacemaker-san` | `debian` (Trixie/13) | `vm` | slot only (§6) |

### 3.2 What is shared vs. arm-specific

**Shared / arm-agnostic (the large majority — and most of the in-flight work):**

- `topology.yaml` (already carries both platforms) and the rendered inventory
- `mqlab vm` / `net` / `obs` command groups, the renderer, transcript, state
- The observability stack (Grafana/Prometheus/`mq_prometheus`), the MQ Service
  panel, log streaming (#143), time-sync (#186)
- Declarative `pymqrest` content, the `dtcc-sim` fixture, the `app-client`, the
  distributed QM-to-QM architecture model
- The `mqlab dr` **Python** module (RPO / exposure / reconcile / ledger /
  classifier accounting — message-loss-window *semantics*, not cluster mechanics)

**Arm-specific backend (the only divergence — a thin, well-bounded interface):**

- **L0 host-prep:** `apt`/`.deb` vs `dnf`/`.rpm`, firewall tooling, service
  management, and the RDQM DRBD **kmod** kernel-version match.
- **HA formation:** Corosync/Pacemaker/STONITH + SAN/iSCSI + DRBD
  (`pcmk-cluster`, `pcmk-stonith`, `drbd-san`, `mq-pcmk-qmgr`) vs
  `rdqm.ini` + `rdqmadm` + `crtmqm -sx` (`rdqm-install`, `rdqm-ha`).
- **DR cutover/failback:** DRBD-async + Booth ticket arbitration (`site-pcmk-dr`)
  vs `crtmqm -rr` + `rdqmdr` (the frozen `site-rdqm` DR path).

### 3.3 The backend contract (stable verbs)

The `mqlab qm` / `ha` / `dr` command groups dispatch to the backend selected per
setup in topology. The verbs are the authoritative design's §8.3–8.5 "common
verbs across both arms" — stable surface, backend-specific implementation:

- **Lifecycle:** `host-prep`, `qm create` / `destroy` / `up` / `down`
- **HA:** `form-group`, `add-node` / `evacuate-node`, `status`, `failover`
- **DR:** `dr bootstrap`, `cutover`, `failback`, `status`
- **Diagnostics:** `runmqras`/FFST capture (host-level) + live QM state (REST)

The existing Pacemaker implementation becomes the **reference backend** behind
this contract (refactored, no behavior change). The RDQM backend is the second
implementation of the same contract. The registry must not bake in a 2-backend
assumption anywhere, and the substrate axis must not assume `vm` (Native HA's
container/K8s substrate reshapes a few fault primitives — see §6).

## 4. The parity harness (approach C — the executable contract)

Parity is *defined by an executable contract* and enforced, not asserted by prose.

### 4.1 What it asserts

- **Capability matrix:** every operator verb (§3.3) works on each arm —
  `form-group`, `failover`, `evacuate`, `cutover`, `failback`, `status`,
  diagnostics capture. Arms are columns; unimplemented cells are explicit
  (`not-yet`), never silently absent.
- **Behavioral assertions (§3.1 of the authoritative design), per arm:**
  1. `kill -9` the QM → automatic restart/failover per design
  2. hard node power-off → failover, **RPO 0 within site**
  3. sever heartbeat/replication → **no split-brain**, correct quorum decision
  4. sever shared storage → graceful, **no corruption**
  5. planned failover **and failback**
  6. rolling patch / node maintenance
  7. **full-site DR** → cross-site cutover + app-level reconciliation
     (authoritative design §4.3)
  8. **diagnostics capture under fault** → a clean IBM-grade bundle
  9. **planned, reversible site role rotation** (authoritative design §4.7)

### 4.2 Parity, defined

**Parity = identical capabilities present + identical correctness outcomes**
across arms — RPO 0 within site, no split-brain, no corruption under fault, clean
diagnostics, cutover/failback both directions. Per the authoritative §6,
**RTO/RPO are recorded but RDQM wall-clock timing is reported qualitatively
only** — never numerically compared to the arm64 Pacemaker arm. Emulation jitter
that produces behavior indistinguishable from a real fault (spurious fencing,
split-brain) is the authoritative design's §6 wire: **stop and surface**, do not
tune timers to mask it.

### 4.3 Why C leads (regression net)

The harness is stood up and made **green against today's working Pacemaker arm
*before* the §5 refactor**. That locks current behavior as a contract and is the
safety net under the approach-A extraction. RDQM rows start `not-yet` and turn
green in P4 — and that green state *is* the first deliverable.

## 5. Phasing

| Phase | Outcome | Notes |
|---|---|---|
| **P0 — Wrap-up** | In-flight worktrees finished and landed; clean `develop`. | #143 log-streaming, #186 time-sync, #169 dr-commands, #175 ha-commands, #177 cluster-cockpit, #62 pcmk-flow. Treated as the **shared tooling layer**; where they touch HA/DR surfaces, land arm-generic where cheap, Pacemaker-backed for now. Docs reframe (this spec + the superseding section) runs concurrently. |
| **P1 — Parity harness vs. Pacemaker** | Cross-arm harness + capability matrix **green on the Pacemaker backend**. | Approach C first, as the regression net. RDQM rows = `not-yet`. |
| **P2 — Extract the arm-backend interface** | Pacemaker logic refactored behind the §3.3 contract; P1 harness stays green. | Approach A. No behavior change; the seam now exists with one conforming backend. Registry written **open** (N backends, N OS platforms, VM-or-container substrate). |
| **P3 — RDQM backend to parity** | `rdqm-install`/`rdqm-ha`/`rdqmdr` ported into the seam; `QMRDQM` wired as the in-house HA substrate of the distributed architecture (`QMRDQM` ↔ `QMDTCC`); observability + declarative content online for RDQM. | Subject to the **cold-rebuild acceptance gate** — done only after a full cold rebuild proves it one-pass, not just lint-green. The frozen RDQM roles predate the tooling era; treat as "proven concept, re-validate," not "known-good." |
| **P4 — Drive RDQM rows green = full parity** | The harness's RDQM rows go green: same capabilities + correctness as Pacemaker. | This green state **is** the first deliverable (G1). |
| **P5 — Continuous parity** | Both arms co-tested; the harness runs across both backends in validation/CI. | Cadence bounded by the CPU/offline constraints (§7). Phase E recast: re-assert the comparison from harness output on demand. |

## 6. Deferred slots (strategic, not built)

Two more arms are coming; the abstraction must accommodate them now, but **no
build effort is spent on them in this pivot** (non-goal, §2).

- **`nativeha-rhel` — IBM MQ Native HA on RHEL/Linux.** The firm already runs
  this; it is un-parked from §2.3 arm 3. **Architecturally the odd one out:** it
  is **container/Kubernetes-based log-replication quorum**, *not* the bare-VM +
  DRBD + multi-NIC substrate the other three share. Leaving the slot honest means
  **not baking `vm` assumptions** into the topology schema, the capability matrix,
  or the fault primitives — a few primitives change shape on it (e.g. "power off
  the node" → "kill the pod / evict the node"). It also carries a **research/
  learning cost** (new to us) that we deliberately do not pay now. Its replication
  model is conceptually closest to RDQM's "data layer owns replication" virtue.
- **`pcmk-debian` — Pacemaker/SAN on Debian (Trixie / 13).** The firm's actual
  base OS is **Debian, not Ubuntu**. This is the *same mechanism* as the built
  `pcmk-ubuntu` arm with a different L0; because Ubuntu is Debian-derived, the
  `apt`/`.deb`/systemd host-prep carries over with near-trivial change. Mostly an
  OS-platform registry entry + a host-prep variant. Ubuntu stays supported
  alongside it.

**Guardrail (restated):** the cost the deferral imposes on *this* pivot is only
that the §3 abstraction and the §4 harness are written **open** rather than
closed to two arms — a constraint on *shape*, not extra build scope.

## 7. Risks & mitigations

- **Refactor regression (P2).** Mitigated by sequence: P1 harness green on
  Pacemaker *before* the approach-A extraction is the net.
- **RDQM arm bit-rot.** The proven roles predate the entire tooling era and may
  have drifted against current `topology.yaml` / inventory / `mqlab`. Treat as
  "proven concept, re-validate on cold rebuild," not "known-good"; the P3
  cold-rebuild gate catches it.
- **TCG emulation jitter.** Spurious fencing/split-brain from emulation is the
  authoritative design's §6 wire — stop and surface; escalate that arm to
  cloud-x86 only if jitter becomes indistinguishable from a real fault. Never
  tune timers to mask it.
- **CPU/RAM budget.** Six TCG RDQM nodes + the Pacemaker arm + obs + fixtures
  likely will **not** all run *simultaneously* on the ~12-core envelope. "Run both
  in parallel" means **both maintained and co-testable**, with heavy full-fleet
  drills possibly **alternating** per arm rather than truly concurrent. The P5
  parity-harness cadence must be realistic about this.
- **Parity drift over time.** The capability matrix + CI enforcement is the
  standing guard — the whole reason C leads.
- **Substrate assumption leakage.** If `vm` gets baked into the topology schema or
  fault primitives during P2, the Native HA slot becomes a retrofit. Reviewed as
  an explicit P2 acceptance check.

## 8. Conventions honored

- **Cold-rebuild acceptance gate** for any lab bring-up/provisioning change (P3).
- **Ansible over shell** for sysadmin-style orchestration; the CLI wraps
  commands-or-playbooks, never cobbles shell scripts.
- **`uv` is a build tool, not runtime** — companion tools invoked by bare name via
  `$PATH`, never `uv run` in runtime paths.
- **Layered error vocabulary** — `mqlab`'s own messages name fully-qualified
  `mqlab` commands; underlying tools (`rdqmadm`, `pcs`, `crtmqm`) speak for
  themselves.
- **Fail loud** — no swallowed exceptions, no fallbacks that hide errors.

## 9. Definition of done (this spec's scope)

- The "Decision & Pivot" superseding section is in the authoritative design doc.
- This spec is committed and reviewed.
- An implementation plan (or plans) covering P0–P5 is written via the
  writing-plans step, with P0 (wrap-up) and P1 (harness-first) as the entry
  points and the deferred slots (§6) explicitly out of build scope.
