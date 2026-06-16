# RDQM Parity Build — arm-backend seam + RDQM distributed-parity + 3+3 DR — Design

> **Status:** design, ready for planning (pushback-hardened).
> **Date:** 2026-06-15
> **Author:** Phillip Moore (with Claude)
> **Tracking issue:** #199. **Builds on:** the pivot design
> [`2026-06-15-rdqm-parity-pivot-design.md`](2026-06-15-rdqm-parity-pivot-design.md)
> (this is its P2+P3, folded together).

---

## 1. Goal

Bring RHEL/RDQM to **parity with the pcmk `distributed` setup** (app → in-house HA
QM ⇄ QMDTCC over net-ext, driven by `mqlab`) **and finish the 3+3 DR**, so RDQM
becomes the primary, fully-tooled arm. "Works the same as distributed" is the bar:
the same `mqlab` command surface, the same DTCC/app message path, with the
in-house QM substrate provided by RDQM instead of Pacemaker.

This **folds the pivot's P2 into P3**: clean `mqlab` RDQM support *requires* the
arm-backend seam, so the seam is built as RDQM is added rather than as a separate
phase. (Decision: confirmed in brainstorming.)

**Non-goals:** a `pymqrest` declarative-content layer (the current distributed
setup does not use one — §5); P1's fault-drill suite (a separate thread);
the Native HA / Debian arm slots (future, per the pivot spec); cleanup of the
stale `content/` dir.

## 2. The arm-backend seam (data-driven registry)

The chosen seam (brainstorming approach A): a **data-driven arm registry** in
`topology.yaml`, consumed by a small resolver. The three-way split from the pivot
spec holds — **catalog** (committed registry), **selection** (runtime, by setup
name), **mechanics** (Ansible/scripts).

`topology.yaml` gains an **`arms:`** block — each arm declares its identity and its
verb implementations:

```yaml
arms:
  pcmk-ubuntu:
    mechanism: pacemaker-san
    os: ubuntu2404-arm64
    substrate: vm
    verbs:
      qm-create:  { playbook: site-pcmk-qm.yml }
      qm-up:      { pcs: "resource enable mq_group" }
      qm-down:    { pcs: "resource disable mq_group" }
      qm-status:  { pcs: "status resources" }
      dr-cutover: { script: pcmk-dr-cutover.sh }
  rdqm-rhel:
    mechanism: rdqm
    os: rhel96-x86_64
    substrate: vm
    verbs:
      qm-create:  { script: rdqm-qm-create.sh }
      qm-up:      { cmd: "<verified-in-Plan-B>" }    # §5 / Issue [5]
      qm-down:    { cmd: "<verified-in-Plan-B>" }
      qm-status:  { cmd: "<verified-in-Plan-B>" }
      dr-cutover: { script: rdqm-dr-cutover.sh }      # new — §6
```

A resolver `src/mqlab/arms.py` reads the registry and answers "for setup X, verb V,
what runs?" Each `setup` names its `arm`. `mqlab qm`/`dr`/`provision` resolve the
arm and dispatch — replacing today's hardcoded `pcs`/playbook calls. The resolver +
registry parsing are **pure-Python, TDD, 100% branch coverage** (like P1). Adding
arm #3/#4 later is another registry row, no new dispatch code.

## 3. Namespacing

Per the pivot spec's rename: `distributed` → **`distributed-pcmk-ubuntu`**; new
**`distributed-rdqm-rhel`** (and its DR sibling, §6). Each names its `arm`. The
`qm:` `QmConfig` (name/vip/vip_ext/dtcc_conn) stays per-setup; the in-house QM name
(`QMPCMK` vs `QMRDQM`) flows from there into the shared layer's `our_qm` var (§4).

## 4. Extract the shared distributed layer (Issue [1])

**Reality:** `site-distributed.yml` line 9 does `import_playbook: site-pcmk.yml` —
the shared DTCC/app/channel plays and the Pacemaker substrate are welded together,
so RDQM cannot reuse it as-is.

**Refactor:** extract the shared plays (the `dtcc`/`app`/`mq-inter-qm` plays,
parameterized by `our_qm`) into **`ansible/site-distributed-shared.yml`**. Then:

- `site-distributed.yml` (pcmk) = `import_playbook: site-pcmk.yml` + `import_playbook: site-distributed-shared.yml`
- **`site-rdqm-distributed.yml`** (new) = `import_playbook: site-rdqm.yml` + `import_playbook: site-distributed-shared.yml`

Both arms provably run the **same** DTCC/app/channel code; only the imported
substrate differs. `our_qm` is threaded from the setup's `QmConfig.name`. This is
the playbook-layer twin of the §2 seam and lands in Plan A (covered by the pcmk
regression net, §8).

## 5. RDQM distributed-parity (Plan B / P3a)

Parity = match what `distributed` **actually does** (Ansible-role object creation +
`mqweb`), not the aspirational `pymqrest` content plane (Issue [3]).

- **New setup `distributed-rdqm-rhel`:** groups `[rdqm_a, dtcc, app]`, arm
  `rdqm-rhel`, qm `QMRDQM` (vip/vip_ext/dtcc_conn mirroring distributed).
- **Provision** (`site-rdqm-distributed.yml`): the RDQM HA substrate
  (`rdqm-install` + `rdqm-ha`) **+** the shared layer (§4) with `our_qm: QMRDQM`.
  The DTCC counterparty QM + the `QMRDQM⇄QMDTCC` channels come from the **existing,
  arm-agnostic `mq-qmgr` / `mq-inter-qm` roles** (they run on `dtcc`).
- **`mqweb`/REST on the RDQM nodes (Issue [3]):** `QMRDQM`, created by
  `rdqm-qm-create.sh` (`crtmqm -sx`), has no `mqweb`. The §1-pivot hard requirement
  (REST on every QM) + parity demand it. **Factor `mq-qmgr`'s mqweb logic
  (`mqweb.service.j2`, `mqwebuser.xml.j2`) into a small reusable `mqweb` role** and
  apply it to the RDQM nodes (per-node, per the pivot §8.3 REST-over-HA model).
- **RDQM QM-lifecycle verbs (Issue [5]):** Plan B's **first task is a spike** —
  bring up RDQM HA, observe and document the real `qm up`/`down`/`status` commands
  (`rdqmstatus -m`, `rdqmadm`, `dspmq`), *then* fill the registry rows from observed
  behavior. The design marks them to-be-verified-empirically, never guessed.
- **Acceptance:** `mqlab run distributed-rdqm-rhel` (the P1 baseline run) goes
  green — app→QMRDQM→QMDTCC→reply round-trips, like distributed on pcmk.

## 6. RDQM DR / the 3+3 (Plan C / P3b)

Net-new tooling (the early Phase-C DR was ad-hoc, never captured; current
`rdqm-qm-create.sh` is HA-only `crtmqm -sx`/`-sxs`). Mirrors the `pcmk-dr-*` pattern
on RDQM mechanics:

- **DR pairing:** `QMRDQM` created with **`crtmqm -rr p`** (primary, site A) /
  **`-rr s`** (recovery, site B). Lands as a new **`rdqm-dr` role** (or a DR mode in
  the QM-create path) wired to the registry's `qm-create` verb for the DR setup.
- **Cutover/failback:** new **`lab/scripts/rdqm-dr-cutover.sh`** driving **`rdqmdr`**
  (`a2b`/`b2a`), the RDQM analogue of `pcmk-dr-cutover.sh`; wired to the registry's
  `dr-cutover` verb. A seed-peer equivalent if RDQM needs the recovery side
  pre-taught (verify in-lab).
- **Setup `distributed-rdqm-rhel-dr`:** groups `[rdqm_a, rdqm_b, dtcc, app]` — two
  3-node sync HA groups, async DR between them; the app is a reconnectable client
  given both VIPs (as the pcmk DR path already is).
- **Scope-honesty (pivot §6):** proves *functional* DR correctness on TCG
  (cutover/failback produce the right state, RPO semantics) — **not** timing.
- **Acceptance:** scripted cutover A→B, run from B, failback B→A.

## 7. `mqlab` CLI surface

Verb surface identical across arms; only dispatch changes (via §2 resolver):

- **`mqlab qm create/up/down/status <setup>`** — refactored to resolve the setup's
  arm → registry verb. Pacemaker behavior preserved (it now routes through the
  resolver instead of inline `pcs`).
- **`mqlab dr <bootstrap|cutover|failback|status> <setup>`** — new command group,
  **arm-generic from the start** (the #169 design informs it; this is its first
  built form), dispatching via the registry.
- **`mqlab vm provision <setup>`** — already setup-based; resolves the arm's
  provision playbook.

## 8. Staging & acceptance gates

Three plans, each independently green (writing-plans produces them in order):

| Plan | Scope | Acceptance gate |
|---|---|---|
| **A — seam + pcmk refactor** | `arms:` registry + `src/mqlab/arms.py` resolver; refactor pcmk `qm`/`provision` through it; extract `site-distributed-shared.yml` (§4); rename `distributed`→`distributed-pcmk-ubuntu`. | resolver unit tests green; **`mqlab run distributed-pcmk-ubuntu` green** (Issue [2] — the automated regression net) + a pcmk cold boot for the substrate. |
| **B — RDQM distributed-parity** | RDQM verb spike (Issue [5]); `mqweb` role + RDQM REST; `distributed-rdqm-rhel` + `site-rdqm-distributed.yml`; RDQM registry rows. | **`mqlab run distributed-rdqm-rhel` green** (one-pass cold rebuild — the §8-pivot cold-rebuild gate). |
| **C — RDQM 3+3 DR** | `rdqm-dr` role (`crtmqm -rr`); `rdqm-dr-cutover.sh` (`rdqmdr`); `mqlab dr` group; `distributed-rdqm-rhel-dr` setup. | scripted cutover/failback A↔B produces correct state (functional, per §6). |

## 9. Testing

- **Pure-Python TDD:** `src/mqlab/arms.py` (registry parse + verb resolution) — 100%
  branch coverage, mirroring P1's `runreport`/`parity`.
- **Lab acceptance:** the cold-rebuild gate per plan (Plan A on pcmk via the
  `mqlab run` net; B/C on RDQM). RDQM lab runs are functional-only (TCG).
- `vrg-validate` (lint/mypy/ty/100%-cov/audit) is the floor on every commit.

## 10. Risks

- **Artifact/box hard gate (blocks B/C).** The RHEL box + DVD ISO + MQ Advanced for
  Developers tar are **not** in `build/`. They must be obtained (Red Hat Developer
  entitlement — the pivot §3.6 "developer-provided artifacts," not yet automated)
  and the box built (`lab/boxes/rhel96/build-box.sh`, slow) before any RDQM
  bring-up. Plan B's entry gate.
- **pcmk refactor regression (Plan A).** Mitigated by Issue [2]: `mqlab run
  distributed-pcmk-ubuntu` green is the automated net (P1 fault-drills remain a
  separate thread).
- **RDQM DR mechanics unproven in current tooling (Plan C).** `crtmqm -rr`/`rdqmdr`
  re-validated live; functional-only on TCG.
- **RDQM QM-lifecycle verbs unknown.** Mitigated by the Plan-B spike (Issue [5]).
- **TCG slowness** throughout the RDQM arm.

## 11. Definition of done (this design's scope)

- This spec committed and reviewed.
- Three implementation plans (A, B, C) written via writing-plans, A first, each
  with its acceptance gate from §8 and the artifact gate (§10) called out in B.
