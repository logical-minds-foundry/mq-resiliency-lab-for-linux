# `mqlab ha` — Intra-Site Pacemaker HA Operation Commands

> **Status:** design, approved in brainstorming 2026-06-14.
> **Date:** 2026-06-14
> **Author:** Phillip Moore (with Claude)
> **Context:** The cross-site DR lifecycle is being made driveable from the CLI
> (`mqlab dr`, `docs/specs/2026-06-14-mqlab-dr-commands-design.md`). Its
> everyday counterpart — operating *intra-site* Pacemaker HA — has no CLI surface
> yet: failover between nodes, draining a node for maintenance, and recovering a
> fenced node are all bare `pcs` today. This design adds **`mqlab ha`**, the
> HA-operations counterpart to `mqlab dr`, so the CLI drives both tiers of
> redundancy with a symmetric vocabulary.
>
> **First pass, by intent.** The real shape of these commands will emerge from
> *operating* the lab — this is a deliberate first cut of the obvious operator
> actions, expected to be refined once we drive it for real. Designed to be easy
> to extend, not to be final.

---

## Contents

- [1. Goal & principles](#1-goal--principles)
- [2. Command surface](#2-command-surface)
- [3. Approach: CLI-direct (not playbooks)](#3-approach-cli-direct-not-playbooks)
- [4. Shared foundation: the topology cluster resolver](#4-shared-foundation-the-topology-cluster-resolver)
- [5. `failover` — move, verify, clear](#5-failover--move-verify-clear)
- [6. `standby` / `unstandby` / `recover`](#6-standby--unstandby--recover)
- [7. `status`](#7-status)
- [8. Testing & validation](#8-testing--validation)
- [9. Scope boundary](#9-scope-boundary)
- [10. Open questions & expected iteration](#10-open-questions--expected-iteration)

---

## 1. Goal & principles

Make intra-site Pacemaker HA *operable* from the CLI: see where the QM runs and
the cluster's health; perform a planned failover to another node; drain/return a
node for maintenance; recover a fenced node.

This completes a three-tier model, all layered on the QM that `qm` creates:

| Tier | Group | Scope |
|---|---|---|
| Lifecycle | `mqlab qm` | does the QM exist / is its resource group enabled (create/destroy/up/down/status) — *exists* |
| Intra-site operate | **`mqlab ha`** | where it runs, move it between nodes, node maintenance — *this spec* |
| Cross-site operate | `mqlab dr` | which site, move it across sites (#169) |

There is intentionally **no `ha bootstrap`** (unlike `dr`): in a single cluster
every node is already a capable target, so there's no standby to ready.

Principles (shared with `dr`): the Python CLI wraps simple direct-command
orchestration *or* playbooks for complex flows; **no `community.general`** (#156);
**no `uv run`** in runtime (#164); **fail-loud + idempotent**; **topology-driven**;
HA *validation* (inducing failures to measure recovery) is out of scope — that's
#149.

## 2. Command surface

```
mqlab ha status     <setup>               # which node runs mq_group; node states (online/standby/offline); resource health
mqlab ha failover   <setup>               # planned move of mq_group off the current node (Pacemaker picks the target)
mqlab ha failover   <setup> --to <node>   # move to a specific node
mqlab ha standby    <setup> <node>        # drain a node for maintenance (resources move off it)
mqlab ha unstandby  <setup> <node>        # return a node to service
mqlab ha recover    <setup> <node>        # bring a fenced/failed node back (cleanup failcounts + stonith)
```

Symmetric with `dr` in verbs and UX (`status`, `failover`), even though the
implementation is lighter (§3).

## 3. Approach: CLI-direct (not playbooks)

Every HA op is **1–3 `pcs` commands on the cluster** — there is no complex,
ordered, cross-host, stateful flow here (one cluster, one `pcs` endpoint). So
they are *simple orchestration of direct commands* → the CLI's job, exactly like
`qm up`/`down`/`status` already do through `_qm_pcs` (run a `pcs` op on the
cluster's first node, stream output, propagate the exit code).

This is the boundary principle applied honestly, not mechanically: `dr failover`
became an Ansible playbook because it *is* a complex multi-host ordered flow;
`ha` ops are none of those, so wrapping each single `pcs` command in a playbook
would be ceremony with no idempotency payoff — and would violate the *other half*
of the rule (don't over-Ansible simple orchestration). So `ha` and `dr` are
symmetric in **vocabulary/UX**, and correctly differ in **implementation** by
complexity.

Mechanism: generalize `_qm_pcs` into a small shared helper that runs one (or a
short, ordered set of) `pcs` command(s) on the resolved cluster's first node,
fail-loud (no `|| true`). `recover`/`failover` are the only multi-step verbs; if
either grows genuinely complex it can graduate to a playbook (the fallback), but
the default is CLI-direct.

## 4. Shared foundation: the topology cluster resolver

Today `_PCMK_CLUSTER_GROUP` is hardcoded to `pcmk_a`. Both `ha` and `dr` need the
target cluster derived from the setup instead. This is a small **shared refactor**
(whichever of #169/#175 lands first builds it; the other consumes it):

- `pcmk_san_ha` → single cluster `pcmk_a`.
- `pcmk_san_dr` → two clusters; `ha` operates on the **live site's** cluster,
  detected the same way `dr` does (`drbdadm role mqlun` → the `Primary` site),
  with a `--site {a|b}` override for the ambiguous / explicit case.

A pure resolver `ha_cluster(setup, site=None) -> cluster_group` (and the node list
for `--to`/`<node>` validation) keyed off `topology.yaml` — unit-tested like the
other topology projections. `pcs` then runs on `<cluster_group>[0]`.

## 5. `failover` — move, verify, clear

`pcs resource move` leaves a `-INFINITY` location ban on the source node. If that
ban is not removed, the resource is pinned and **future automatic HA is broken** —
the classic `pcs` footgun. So `ha failover` is three ordered steps, idempotent:

1. **Move** — `pcs resource move mq_group [<node>]` (no node → ban the current
   node so Pacemaker relocates; `--to <node>` → move to that node). Resolve/verify
   `<node>` is a real cluster member first (fail loud otherwise).
2. **Verify** — wait for `mq_group` to report `Started` on a *different* node
   (bounded wait; fail loud on timeout with the observed state).
3. **Clear** — `pcs resource clear mq_group` to remove the ban, so the cluster can
   fail over freely afterward.

Idempotency: a re-run with a stale move-ban present clears it; a failover when the
group is already on the requested node is a no-op (just ensure no lingering ban).
**`ha status` surfaces a stale move-ban as a warning** so a half-finished failover
is visible, not silent.

## 6. `standby` / `unstandby` / `recover`

Single (or few) `pcs` ops on the resolved cluster, idempotent, fail-loud:

- **`standby <node>`** — `pcs node standby <node>` (Pacemaker drains resources off
  it). Already-standby → clean no-op.
- **`unstandby <node>`** — `pcs node unstandby <node>`. Already-online → no-op.
- **`recover <node>`** — bring a fenced/failed node back: `pcs resource cleanup`
  (clear failcounts, optionally `--node <node>`) + `pcs stonith cleanup`, and
  `pcs cluster start <node>` if Pacemaker isn't running there. The only verb that
  may legitimately become a small playbook if the sequence proves fiddly (§3).

All node arguments are validated against the resolved cluster's member list
before any `pcs` call (fail loud on an unknown node).

## 7. `status`

Read-only aggregation, rendered as a compact table: parse `pcs status` (nodes +
resources) on the resolved cluster into — which node runs `mq_group`, each node's
state (`online`/`standby`/`offline`/`UNCLEAN`), the resource group's health, and a
**warning if a stale `move` location-ban is present**. The parser is a pure
function (input: `pcs status` text → structured state), unit-tested independently
of the live cluster, mirroring how `fleet.py`/`vmstatus.py` parse and render.

## 8. Testing & validation

- **Pure-function tests:** the `pcs status` parser (node placement, node states,
  stale-ban detection); the `ha_cluster(setup, site)` resolver (single-cluster
  `pcmk_san_ha`; live-site + `--site` for `pcmk_san_dr`).
- **CLI tests (RecordingRunner):** each verb issues the right `pcs` command(s) on
  the resolved cluster's first node, with fail-loud propagation, `--to`/`<node>`
  validation, and the move→verify→clear sequence for `failover` — as **bare
  `ansible`/`pcs`** invocations (no `uv run`). Mirrors `test_cli_qm`.
- **Live:** validate every verb against the running `pcmk_san_ha` cluster
  (human-operated).
- All under `vrg-validate`; 100% branch coverage on the Python.

## 9. Scope boundary

**In:** `ha status` / `failover` (+`--to`) / `standby` / `unstandby` / `recover`;
the shared topology cluster resolver; the `pcs status` parser; tests.

**Deferred / out:**
- **HA *validation* experiments** — inducing failures (kill/fence a node) to
  measure recovery/RPO — belong to #149's resilience-validation work, not here.
- **Cluster-wide maintenance mode** (`pcs property maintenance-mode`) — considered
  and left out of the first cut; add if operating the lab shows we need it.
- **RDQM/RHEL** — out (the lab is Ubuntu-first; RDQM has its own verbs).

## 10. Open questions & expected iteration

This is explicitly a first pass; the following will firm up once we *operate* it:

- **`failover` target selection** — let Pacemaker choose (ban-current) vs always
  requiring `--to`? Lean: ban-current by default (let the cluster decide), `--to`
  to force. Revisit once we see how it feels in practice.
- **`recover` exact sequence** — which of cleanup / stonith-cleanup / cluster-start
  are actually needed, and in what order, depends on real fenced-node states we
  haven't catalogued yet. May graduate to a playbook.
- **`status` for a `pcmk_san_dr` setup** — show only the live cluster, or both
  sites side by side? Lean: live cluster by default, `--site`/`--all` to widen.
- **The `_PCMK_CLUSTER_GROUP` refactor** — coordinate with #169 so it's built once
  and shared, not twice.
