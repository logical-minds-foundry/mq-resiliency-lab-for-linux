# JVM Cold-Start Concurrency Audit — bring-up serialization (A)

**Date:** 2026-09-20
**Issue:** #1161 (epic logical-minds-foundry/.github#249 — macOS/arm64 startup
reliability & bring-up hardening)
**Task:** Plan Task 1 (audit/enumerate) — the diagnosis-first input to Task 2
(serialize with the #1151 template). Spec `epics/249-startup-reliability/spec.md`
§4.2 (the PR #1151 template) and §4.3 (the JVM-service inventory) are the grounding.

**Status:** AUDIT COMPLETE (static code analysis). Every code fact below is cited to
a `file:line` read of the repo this session; the ~7× nested cold-start tax and the
OpenSearch ~225 s figure are cited to spec §1/§4.3 (this session's earlier
investigation), not re-measured here. Where a conclusion is reasoning on top of the
code rather than a code fact, it is labelled **(judgment)**.

**Live-run confirmation is DEFERRED to VAL-A (#1154)** — the lab is human-operated
and the agent cannot boot it. Plan Task 1 Step 2 ("instrument one cold bootstrap")
is therefore satisfied here by static analysis plus the already-recorded cold-boot
evidence trail (#1022/#1034/#1040); the instrumented cold-boot proof (serialized
cold-starts, no timeout warnings) is VAL-A's acceptance, and this note flags it as a
deferred confirmation rather than asserting a live result.

---

## 1. Scope & method

The goal is to establish empirically *where* ≥2 JVM-heavy services
(Liberty/mqweb, OpenSearch, OpenSearch Dashboards, Data Prepper) cold-start
**concurrently** — per node and cross-node within a bootstrap phase — so that
Task 2 serializes exactly those points with the proven #1151 template and no more
(spec §3: diagnose before fixing; §4.2: generalize the pattern, do not invent one).

Method: direct read of the four-phase bootstrap driver, the mqweb plays and role,
the logsearch bring-up path (playbook + the three JVM roles' `configure` halves),
and the systemd unit `Type`/budget declarations. The nested-virt JVM cold-start tax
itself is not re-litigated — it is established in spec §1 (CPU-active-but-slow, not
I/O-bound; ~7× the cloud; OpenSearch ~225 s on 6 vCPU under nested virt).

---

## 2. The #1151 template being generalized (spec §4.2)

PR #1151 (commit `9c54319`) serialized mqweb on the one Ubuntu Native-HA play with a
four-part shape:

1. **Non-blocking start** — `systemd … no_block: true`
   (`ansible/roles/mqweb/tasks/main.yml:69`), so provision never blocks on the JVM.
2. **Generous, bounded systemd budget** — `TimeoutStartSec=1800`
   (`ansible/roles/mqweb/templates/mqweb.service.j2:13`).
3. **Play-level `serial: 1`** — `ansible/_nativeha-ubuntu-cluster-ha.yml:34`, so the
   nodes cold-start one at a time (~20 s isolated) instead of together.
4. **Bounded, non-fatal readiness gate** — the role's last task, `wait_for` port 9443
   `timeout: 300` with `failed_when: false` + a `debug` warn keyed on `elapsed >= 300`
   (`ansible/roles/mqweb/tasks/main.yml:103-119`).

Parts 1, 2 and 4 live in the **shared** `mqweb` role, so they already apply to every
play that includes it. **Only part 3 (`serial:`) is per-play** — that is the
generalization gap (spec §4.2).

---

## 3. Enumerated concurrent JVM cold-start points

### 3.1 mqweb across QM nodes — REAL gap (the fix target)

**Data.** Eight plays include the `mqweb` role. Before this change only
`_nativeha-ubuntu-cluster-ha.yml:34` carried `serial: 1`; the other seven ran mqweb
in parallel across every host in their target group:

| Play | Host group | `serial:` before | `serial:` after |
|---|---|---|---|
| `_nativeha-ubuntu-cluster-ha.yml` | `nha_ubuntu_a` | `1` (template) | `1` |
| `_nativeha-cluster-ha.yml` | `nha_rhel_crr_a` | — | `1` |
| `_nativeha-dr-replication.yml` | `nha_rhel_crr_b` | — | `1` |
| `_nativeha-ubuntu-dr-replication.yml` | `nha_ubuntu_b` | — | `1` |
| `_pcmk-cluster-ha.yml` | `pcmk_a` | — | `1` |
| `_pcmk-dr-replication.yml` | `pcmk_b` | — | `1` |
| `_rdqm-cluster-ha.yml` | `rdqm_a` | — | `1` |
| `_rdqm-dr-replication.yml` | `rdqm_b` | — | `1` |

Each mqweb play is a **dedicated** play (`roles: [mqweb]` and nothing else), so a
play-level `serial:` scopes only to mqweb and does not throttle any co-located role.
Ansible runs plays sequentially, so within a provision run the site-A and site-B
mqweb plays (DR variants) already run one after the other; `serial: 1` closes the
remaining gap — parallel cold-start *within* a single group's 2–3 nodes.

**Judgment.** This is the concurrent-JVM point that PR #1151 fixed for one play and
that spec §4.2 names as the generalization gap. It is the genuine fix target of
Task 2 Step 1. `serial: 1` matches the template and the isolated-mqweb ~20 s
come-up; there is no evidence a 2-vCPU QM node can absorb two Liberty cold-starts at
once, so `serial: 1` (not a higher `N`) is the evidence-sized value (spec §8: the
lever is sized to evidence, not dogma).

### 3.2 logsearch JVM trio — ALREADY serialized (no code change required)

**Data.** The logsearch node hosts three JVMs — OpenSearch, Data Prepper, OpenSearch
Dashboards — on 2 vCPU (`lab/topology.yaml`), and spec §4.3 flags it as "the single
densest concurrent-JVM point." But the bring-up path does **not** start them
concurrently. `site-logsearch.yml` is a single `hosts: logsearch` play that includes
the three roles' `configure` halves **in a fixed, blocking sequence**
(`ansible/site-logsearch.yml:19-48`):

1. `opensearch` configure — `systemd enable+start` then a **fatal, blocking**
   `uri _cluster/health` readiness gate, `retries: 180 × delay: 5 = 900 s`, until
   `green|yellow` (`opensearch/tasks/configure.yml:20-45`).
2. `data-prepper` configure — `systemd enable+start` then a **fatal, blocking**
   `wait_for` port 21892, `timeout: 900` (`data-prepper/tasks/configure.yml:14-33`).
3. `opensearch-dashboards` configure — `systemd enable+start` then a **fatal,
   blocking** `uri /api/status` gate, `180 × 5 = 900 s`
   (`opensearch-dashboards/tasks/configure.yml:35-60`).

All three units are `Type=simple` (`opensearch/tasks/install.yml:108`;
`opensearch-dashboards/tasks/install.yml:85`; `data-prepper/tasks/install.yml:91`),
so each `systemd state: started` task returns as soon as the process is forked — but
the **readiness gate immediately after each start blocks the play** until that
service is actually up. Because Ansible runs a play's tasks strictly in order, task 2
(data-prepper start) cannot begin until OpenSearch's health gate has returned, and
task 3 (Dashboards start) cannot begin until Data Prepper's port gate has returned.
The three JVM cold-starts are therefore **already staggered one-at-a-time in
dependency order** (OpenSearch → Data Prepper → Dashboards).

**Corroborating recorded evidence.** The already-recorded cold-boot trail confirms
sequential (not concurrent) execution: cold-boot run #1022 "got past OpenSearch
(widened in #1034) only to time out here [at Data Prepper] at the old 180 s"
(`data-prepper/tasks/configure.yml:24-27`). Data Prepper could only "time out *after*
OpenSearch passed" if the two ran in sequence — which is exactly what the blocking
gates enforce. Had they run concurrently, both would have been mid-cold-start
together.

**Judgment.** Spec §4.3's description of the trio as starting "concurrently" is an
overstatement of the risk: the code already realizes the plan Task 2 Step 2
prescription ("OpenSearch reaches its readiness gate before Dashboards + Data Prepper
cold-start"). It is in fact *more* conservative than the prescription — the plan
allows Dashboards + Data Prepper to come up in parallel after OpenSearch, whereas the
play serializes all three. **No code change is required for the logsearch trio**, and
inventing one would violate spec §4.2 ("do not invent a new mechanism") and risk
regressing a working, load-bearing ordering. The serialization is intentional and
documented in `site-logsearch.yml:22` ("ORDER is load-bearing"); this audit records
the finding so the ordering is not later parallelized or its gates weakened. The
one latent (out-of-scope) concurrency risk — all three units are left
`enabled: true`, so a *subsequent VM reboot* would auto-start them concurrently — is
not on the cold-bootstrap path (the baked box is inert and first bring-up runs the
serialized play) and belongs to the follow-on set, not this epic.

### 3.3 Cross-node / cross-phase JVM overlap — none within a phase

**Data.** The observe phase prepends the logsearch bring-up to its own steps
(`src/mqlab/cli.py:973`, `steps = _logsearch_up_steps() + steps`), and `run_steps`
executes them strictly in order. The logsearch trio therefore fully completes (all
three fatal gates passed) **before** the obs-stack steps (`site-obs.yml`,
Prometheus + Grafana) run. Prometheus and Grafana are Go binaries, not JVMs, so even
if they overlapped there would be no JVM-tax concurrency. mqweb comes up in the
**provision** phase and the logsearch trio in the **observe** phase — different
phases, run sequentially by the phase runner (`src/mqlab/phases.py:646`) — so there
is no mqweb↔logsearch JVM overlap. The qm (queue manager) is not a JVM.

**Judgment.** There is no remaining cross-node or cross-phase concurrent-JVM
cold-start once §3.1 is fixed. The only concurrent-JVM point that needed a code
change was the seven mqweb plays.

---

## 4. Prescription (input to Task 2)

| Point | Finding | Prescription |
|---|---|---|
| 7 mqweb plays w/o `serial:` (§3.1) | Real concurrent cold-start across each group's 2–3 QM nodes | Add play-level **`serial: 1`** to each — generalize #1151 part 3. Parts 1/2/4 already apply via the shared role. **Done in Task 2 Step 1.** |
| logsearch JVM trio (§3.2) | **Already serialized** in dependency order by the sequential fatal readiness gates in `site-logsearch.yml` | **No code change.** Preserve the load-bearing ordering and each service's bounded gate; audit records it so it is not later parallelized. |
| cross-node/phase overlap (§3.3) | None (logsearch completes before obs; obs stack is non-JVM; mqweb and logsearch are in different, sequential phases) | No change. |

Fatality is intentionally **not** touched here — right-sizing budgets and setting
fatality by data-path dependence is Task 3 (#1162). This task changes only
serialization (spec §5 A vs B split).

---

## 5. Deferred confirmation (VAL-A #1154)

`vrg-validate` (the lint/render/coverage gate) is necessary but **not sufficient**
for acceptance (spec §7; repo cold-rebuild doctrine). The empirical proof that the
cold bootstrap now shows serialized (not concurrent) cold-starts with **no timeout
warnings** is VAL-A's 5-consecutive-clean-cold-boot gate on the Apple-silicon host,
run by the human. This audit's static-analysis findings — that the mqweb plays were
the one real gap and the logsearch trio was already serialized — are the hypotheses
VAL-A confirms against a live instrumented run.
