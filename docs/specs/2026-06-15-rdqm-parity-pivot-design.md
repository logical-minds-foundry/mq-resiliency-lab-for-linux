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

The platform decision is made: the app standardizes on **RHEL + RDQM**, for
**IBM-supportability** reasons — the criterion the authoritative design already
weighted heaviest (§2.7, §3). The R&D comparison did its job and pointed where
the app independently landed.

But the lab's history left RDQM in an awkward place. The **RDQM substrate was
built and proven first** (PR #36 / issue #31): a RHEL 9.6 kickstart box, the
`rdqm-install` and `rdqm-ha` roles, a live `QMRDQM` on a floating VIP, the §3.1
fault suite at 4/4, and the full 3+3 `rdqmdr` cutover/failback with RPO 0 in both
directions. Then it **froze**. Every piece of tooling sophistication since — the
`mqlab` orchestrator CLI, the net/vm lifecycle, the observability stack
(Grafana/Prometheus/`mq_prometheus`), the distributed QM-to-QM architecture
(`QMPCMK` ↔ `QMSVC`), declarative `pymqrest` content, the MQ Service panel —
grew against the **Pacemaker/Ubuntu** arm. #169 even explicitly "set RDQM-DR
aside" and scoped the new `mqlab dr`/`ha` command groups to Pacemaker only.

So RDQM is **proven but primitive**, and the real RHEL work is **not resuming an
unfinished build** — it is **bringing the matured tooling onto the proven RDQM
substrate** and making RDQM the fully-tooled, primary arm.

## 2. Goals & non-goals

### Goals

- **G1 — RDQM at full parity** with what the Pacemaker arm demonstrates today:
  the distributed architecture with **`QMRDQM` as the app HA substrate**
  (`QMRDQM` ↔ `QMSVC` across the simulated WAN), the app trade flow,
  observability, declarative `pymqrest` content, and the §3.1 fault + DR drills —
  all on RHEL.
- **G2 — Parity as a standing property, not a snapshot.** Both arms are
  co-maintained and co-tested **serially** going forward (sequential by default,
  parallel-capable by design — §3.4); the Phase-E comparison becomes a
  re-assertable result computed from recorded harness output, not a one-shot
  writeup and not a requirement that both fleets co-reside.
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
- **No concurrent multi-arm execution.** Arms run one at a time (§3.4);
  parallelism is an explicit non-goal — it complicates every operational surface
  (dashboards, the run report) for no evidentiary gain, since parity is a
  comparison of recorded results (§4.4), not live co-residency.

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
- Declarative `pymqrest` content, the `svc-sim` fixture, the `app-client`, the
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

**Catalog vs. selection vs. mechanics — a deliberate three-way split.** Three
different things must never be conflated:

- **The catalog of what *can* be built** — the arm registry and the setups that
  compose it — is a **committed declaration** in `topology.yaml` (extending its
  existing `boxes` / `groups` / `setups` / platform model). Each `setup` names its
  `arm`; each `arm` declares `(mechanism-backend, os-platform, substrate)`. This is
  a *menu*; committing the menu is correct.
- **The selection of what to build or run *right now*** is a **runtime argument**
  to the `mqlab` commands — you name the arm-scoped setup at invocation
  (`mqlab … distributed-rdqm-rhel` vs `… distributed-pcmk-ubuntu`), exactly as
  setup names already drive `mqlab net` / `vm` / `obs` / `provision` today
  (e.g. `mqlab vm provision <setup>`, #105). Choosing an arm is **never an
  edit-and-commit** to a config file: the commands can build *any* catalogued arm,
  and the unique name on the command line is what determines which.
- **The mechanics of *how* to build each** live in the Ansible roles, templates,
  and the per-arm backends (§3.2) — committed, and shared across arms wherever the
  §3.3 contract lets them be.

This split is *why* arm-namespaced identity (§3.4) is load-bearing: the unique
name is the runtime selector. The `run` operation (§4.4) takes the same setup name
as its argument and records it in the report.

### 3.4 Sequential operation & arm-namespaced identity

**Operating model: one arm at a time — sequential, app.** The lab runs a single
arm's setup at a time. The host cannot TCG-emulate two full RDQM fleets, the
machine is shared with other work, and — decisively — **running arms concurrently
buys little and costs much**: it complicates the dashboards, the report, and every
operational surface for no evidentiary gain, because parity is a *comparison of
recorded results* (§4.4), not a side-by-side live spectacle. **Concurrent
execution of multiple arms is an explicit non-goal** (§2). We build each flavor
once, run it on its own, and record what it did.

**Arm-namespaced identity (still required — for a different reason).** Sequential
operation does *not* excuse single-arm naming. Today the `distributed` setup is
implicitly *the Pacemaker one*, and the shared `net-data-a` / `10.10.1.100` VIP is
a latent collision the moment RDQM returns. Clean per-arm identity is what makes
the **stop/restart/toggle** story (§3.5) work and keeps one arm's state from
clobbering another's. The refactor (P2/P3) gives every arm a disjoint namespace:

- **Setups** named `<workload>-<arm>` — e.g. `distributed-pcmk-ubuntu`,
  `distributed-rdqm-rhel` — never a bare `distributed`.
- **Disjoint per-arm:** node names (already arm-prefixed: `pcmk-*`, `rdqm-*`);
  networks and VIP/data subnets (breaking the shared `net-data-a` / `.100` VIP so
  each arm owns its space); QM names (`QMPCMK` vs `QMRDQM`); container/fixture names.
- **Shared fixtures stay shared only by explicit intent** (e.g. one `svc-sim`
  counterparty) — never by namespace accident.

**Supporting VMs ride the fastest base; only the core-under-test pays the slow
tax.** The instrumented core is the **3+3 HA/DR group** (plus a few support VMs as
the tooling grows). Everything *outside* that core — `obs`, `mon-probe`,
`svc-sim`, `app-client` — should use the cheapest/fastest-to-build platform, which
today is **Ubuntu arm64 (KVM-accelerated)**. *Verified 2026-06-15:* only the six
`rdqm-*` nodes carry the `rhel96-x86_64` (TCG) override; every other node —
including the Pacemaker arm's own nodes — already inherits the Ubuntu arm64
default. So when the RDQM arm is under test, only its six core nodes are slow x86;
the whole support cast stays fast. Keep it that way: never put a support VM on a
slow platform.

### 3.5 Cached, toggleable per-arm state — restart without rebuild

Iterating across arms must not mean rebuilding each from scratch every time. The
target workflow: **build the RDQM environment, run it, shut it down; build the
Pacemaker environment, run it, shut it down; later restart either from its cached
state** — toggling between arms cheaply.

- **Persistent across a base-VM *restart*, disposable across a *rebuild*.** A
  setup's VMs and generated config survive stopping and restarting the core Lima
  base VM; they are *not* expected to survive a full base-VM rebuild (we still
  rebuild aggressively — every few days, as with the dev and MQ-tooling VMs).
- **Per-arm cached state under `build/`, keyed for recall.** Structure the
  `build/` snapshots so a run is addressable as "this tooling, this generated
  config, this commit, on this date" and can be **stopped and restarted by
  reference** — an RDQM env in one cache slot, a Pacemaker env in another, each
  independently start/stop-able. This extends the state consolidation already
  scoped in **#167** (Vagrant state → `build/` via `VAGRANT_DOTFILE_PATH`, reset
  semantics) into a **per-arm, snapshot-addressable** layout, and shares
  coordinates with the run report (§4.4).
- **Ephemeral, not precious.** The cache is a toggling convenience, never a source
  of truth — the source of truth is the committed tooling + generated config; any
  cache can be discarded and regenerated.

### 3.6 Developer-provided, entitlement-gated artifacts

Some inputs **cannot** live in the repo — they are large and/or entitlement-gated,
and the secrets policy forbids committing any MQ entitlement/license artifact. For
the RDQM arm these are the **RHEL DVD ISO** (~12.7 GB) and the **IBM MQ Advanced
for Developers tar** (~520 MB). The lab is built **from the outside, by a developer
with their own entitlements** — the author is a contractor with deliberately
limited access at the app and leans on this personal laptop lab to prove concepts
— so artifact provisioning is the developer's responsibility, **by design**.

**Mechanism — a machine-local user config, never committed.** A user-level config
file (e.g. `~/.config/mq-cluster-tooling/config.toml`) declares **where each
required artifact lives on this machine** — the RHEL ISO path, the MQ dev tar path,
and any future arm's equivalents. It is:

- **Not in git** — machine-local and per-developer, the same trust boundary as
  `build/` and the secrets policy.
- **Not topology** — `topology.yaml` declares the *catalog* (§3.3); the user config
  supplies *this developer's local artifact paths*. Selection stays runtime;
  artifact location stays machine-local.
- **Flexible on location** — artifacts may sit in `build/` (where they are today)
  or anywhere else the developer points to.

**The repo ships pointers, not artifacts.** Committed docs tell the next person
*how to obtain* the inputs — sign up for a **Red Hat Developer** subscription (the
no-charge Developer-edition path RDQM uses), where to download the RHEL ISO and the
MQ Advanced for Developers tar — and *how to configure* their paths. Taking
responsibility for obtaining entitlements is **left to the developer** (the author,
or anyone who later uses this). The lab stays buildable by anyone with their own
entitlements, with zero licensed bits in the repo.

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

### 4.4 The single-invocation `run` and the report corpus

The harness contract (§4.1) is exercised by a **single named operation** that
drives a whole arm end to end and emits a durable report. This is the deliverable's
beating heart — the thing you invoke after a rebuild and walk away from.

**One invocation, full sweep.** Post-rebuild, the `run` operation drives the entire
pipeline for one arm: bootstrap → `net` + `vm` bring-up → provision → setup (form
the 3+3 group, wire the distributed `QM*` ↔ `QMSVC` mesh, bring up obs and
declarative content) → drive the §4.1 fault + DR drills → tear down. It promotes
the existing end-to-end test script and the HA/DR experiment-runner idea (#119)
into a first-class, named, repeatable operation.

**A timestamped, self-describing report per run**, capturing both halves of an
experiment:

- *Inputs (what was tested):* the arm/setup, the **generated `net`/`vm`/setup
  config actually used**, tool/box/MQ versions, and the **exact code commit SHA** —
  so every result refers back to the precise repo state that produced it.
- *Outputs (what happened):* per-drill RTO/RPO/intervention/data-integrity results
  and the capability-matrix verdicts (§4.1), with RDQM timings recorded
  qualitatively per the §6 scope-honesty rule.

**The corpus is the asset.** Each report is a point-in-time snapshot — "we ran
*this* config at *this* commit and got *these* results." Accumulated, they form a
**large, slow, automatable integration-test record**: a `(arm × config × commit) →
outcomes` mapping, re-runnable on demand, that becomes the standing evidence base
behind the Phase-E parity claim. The instrumented core is the **3+3 HA/DR group**;
the report scales as that core grows by a few support VMs. Reports share their
addressing coordinates with the per-arm state cache (§3.5).

## 5. Phasing

| Phase | Outcome | Notes |
|---|---|---|
| **P0 — Wrap-up** | In-flight worktrees finished and landed; clean `develop`. | The genuine in-flight set (verified ahead of `develop` 2026-06-15): **#143 log-streaming, #169 dr-commands, #175 ha-commands, #177 cluster-cockpit, #186 time-sync**. Treated as the **shared tooling layer**; where they touch HA/DR surfaces, land arm-generic where cheap, Pacemaker-backed for now. **Cleanup:** remove the stale worktrees `#141` mq-prometheus-spike (0 commits ahead — spike concluded) and confirm `#62` pcmk-flow is already merged (PR #63, 2026-06-09). Docs reframe (this spec + the superseding section) runs concurrently. |
| **P1 — Parity harness vs. Pacemaker** | Cross-arm harness + capability matrix **green on the Pacemaker backend**; the single-invocation `run` driver (§4.4) emits the timestamped report. | Approach C first, as the regression net. RDQM rows = `not-yet`. The `run` operation drives the Pacemaker arm end to end and writes the first reports. |
| **P2 — Extract the arm-backend interface** | Pacemaker logic refactored behind the §3.3 contract; P1 harness stays green. Arm-namespacing (§3.4) and per-arm state cache (§3.5 / #167) land here. | Approach A. No behavior change; the seam now exists with one conforming backend. Registry written **open** (N backends, N OS platforms, VM-or-container substrate). The `distributed` setup is renamed `distributed-pcmk-ubuntu` and its nets/VIPs de-collided. |
| **P3 — RDQM backend to parity** | `rdqm-install`/`rdqm-ha`/`rdqmdr` ported into the seam; `QMRDQM` wired as the app HA substrate of the distributed architecture (`QMRDQM` ↔ `QMSVC`); observability + declarative content online for RDQM. | **Entry gate (§3.6):** the RHEL ISO and MQ dev tar must be present and path-configured in the user config. Subject to the **cold-rebuild acceptance gate** — done only after a full cold rebuild proves it one-pass, not just lint-green. The frozen RDQM roles predate the tooling era; treat as "proven concept, re-validate," not "known-good." |
| **P4 — Drive RDQM rows green = full parity** | The harness's RDQM rows go green: same capabilities + correctness as Pacemaker. | This green state **is** the first deliverable (G1). |
| **P5 — Continuous parity** | Both arms co-tested **serially**; the `run` → report corpus (§4.4) is the standing mechanism, re-runnable per arm on demand. | Cadence bounded by the CPU/offline constraints (§7) and sequential operation (§3.4). Phase E recast: re-assert the comparison from accumulated report output, never from live co-residency. |

## 6. Deferred slots (strategic, not built)

Two more arms are coming; the abstraction must accommodate them now, but **no
build effort is spent on them in this pivot** (non-goal, §2).

- **`nativeha-rhel` — IBM MQ Native HA on RHEL/Linux.** The app already runs
  this; it is un-parked from §2.3 arm 3. **Architecturally the odd one out:** it
  is **container/Kubernetes-based log-replication quorum**, *not* the bare-VM +
  DRBD + multi-NIC substrate the other three share. Leaving the slot honest means
  **not baking `vm` assumptions** into the topology schema, the capability matrix,
  or the fault primitives — a few primitives change shape on it (e.g. "power off
  the node" → "kill the pod / evict the node"). It also carries a **research/
  learning cost** (new to us) that we deliberately do not pay now. Its replication
  model is conceptually closest to RDQM's "data layer owns replication" virtue.
- **`pcmk-debian` — Pacemaker/SAN on Debian (Trixie / 13).** The app's actual
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
- **CPU/RAM budget — resolved as sequential-by-default (§3.4).** Six TCG RDQM
  nodes + the Pacemaker arm + obs + fixtures will **not** all run simultaneously on
  the ~12-core envelope, and the host is shared with other work. The lab therefore
  runs **one arm at a time** by default; parity is asserted from recorded harness
  output across serial runs, not from both fleets co-residing. Concurrency stays a
  *capability* (arm-namespaced resources, §3.4) — realistic for two arm64 KVM
  Pacemaker arms, expected to exhaust the host for two TCG RDQM fleets.
- **Artifact & entitlement availability (§3.6).** RDQM bring-up depends on
  developer-provided, entitlement-gated artifacts (RHEL ISO, MQ dev tar) being
  present and path-configured. The entitlement basis is the no-charge **Red Hat
  Developer** edition (as Phase C used); revisit only if a licensed/production
  build is ever needed. Mitigated by the §3.6 user-config + committed
  obtain-here pointers; no entitlement artifact ever enters git. (Closes the
  authoritative §11 open risk on RHEL developer licensing.)

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
