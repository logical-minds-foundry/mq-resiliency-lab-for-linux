# `mqlab dr` — Cross-Site DR Lifecycle Commands

> **Status:** design, approved in brainstorming 2026-06-14.
> **Date:** 2026-06-14
> **Author:** Phillip Moore (with Claude)
> **Context:** The Pacemaker/SAN arm's cross-site DR lifecycle currently lives in
> hand-built shell scripts — `lab/scripts/pcmk-dr-seed-peer.sh` (ready the
> standby cluster to run the QM) and `pcmk-dr-cutover.sh` / `pcmk-dr-force.sh`
> (controlled / forced cross-site cutover). They were a good bridge, but they're
> loose, imperative, and full of `|| true` masking — the same silent-failure
> class that cost us repeatedly during the Phase-D bring-up. This design folds
> them into a first-class **`mqlab dr`** command group, so the CLI drives the
> entire DR lifecycle: inspect, ready the standby, fail over, recover.
>
> The arm it operates on is live and healthy (verified 2026-06-14: `pcmk_san_dr`
> built one-pass, `mq_group` Started on site A, DRBD `Primary`/`UpToDate` ⇄
> `Secondary`/`UpToDate`) — so the new commands can be validated against a real
> cluster as they're built.

---

## Contents

- [1. Goal & principles](#1-goal--principles)
- [2. Command surface](#2-command-surface)
- [3. Where the work lives (the CLI ↔ Ansible boundary)](#3-where-the-work-lives-the-cli--ansible-boundary)
- [4. Topology-driven DR parameters](#4-topology-driven-dr-parameters)
- [5. Direction auto-detection & recoverability](#5-direction-auto-detection--recoverability)
- [6. The playbooks](#6-the-playbooks)
- [7. Pre-flight & safety](#7-pre-flight--safety)
- [8. Testing & validation](#8-testing--validation)
- [9. Scope boundary](#9-scope-boundary)
- [10. Open questions](#10-open-questions)

---

## 1. Goal & principles

Replace the DR shell scripts with `mqlab dr` subcommands that are idempotent,
**explicitly recoverable**, fail-loud, and topology-driven.

Governing principles (all confirmed in brainstorming):

- **Sysadmin-style orchestration → Ansible playbooks (native modules).** The
  Python CLI wraps either simple direct-command orchestration *or* a playbook
  when the flow is complex. The tell: if you'd cobble together a shell script,
  it belongs in a playbook (native modules) or the CLI. Native Ansible gives
  idempotency + fail-loud for free; raw `shell` is the last resort (only where no
  module exists), always guarded by `failed_when`/`changed_when`, never `|| true`.
- **No `community.general`** — ansible-core only (#156).
- **No `uv run` in runtime** — the CLI invokes `ansible-playbook`/`ansible` by
  bare name on `$PATH` (#164).
- **Topology is the single source of truth** — per-site DR parameters are a pure
  function of `lab/topology.yaml`, not hardcoded (as they are in the scripts).
- **A DR failover is an outage, not a transparent event.** It is unstable until
  complete and demands verification. So the design optimises for *recoverability*
  (re-run to completion from any partial state), not for one-shot convenience.

## 2. Command surface

```
mqlab dr status     <setup>                # live site, DRBD health, mq_group, both VIPs (read-only)
mqlab dr bootstrap  <setup>                # standby QM: definition + disabled unit — capable, not running
mqlab dr failover   <setup>                # auto-detect live → standby
mqlab dr failover   <setup> --force        # peer dead: force-promote, accept bounded RPO>0
mqlab dr failover   <setup> --to {a|b}     # required ONLY when auto-detect can't resolve direction
```

- **`status`** is read-only and load-bearing: it's both the operator's "where am
  I" and the disambiguator that makes the failover direction obvious.
- **`bootstrap`** readies the standby (the old `pcmk-dr-seed-peer.sh`): teach the
  peer cluster the QM exists (`addmqinf`) and install a **disabled** systemd unit,
  so a failover can start it. Capable, not running. (Named `bootstrap`, not
  `seed`, because it makes the standby *runnable*, not just plants data.)
- **`failover`** is the verb (not cutover/failback): it moves the QM from the
  live site to the standby. Direction is **auto-detected** (§5); the positional
  target is unnecessary on the normal path.
- **`--force`** folds in `pcmk-dr-force.sh`: for a dead peer, force-promote and
  accept a bounded RPO > 0 (relaxing the no-data-loss pre-flight), with a
  fail-loud `role:Primary` assertion afterward (the #67 lesson).

## 3. Where the work lives (the CLI ↔ Ansible boundary)

This split is the concrete answer to "when does it go in the CLI vs a playbook":

| Command | Home | Why |
|---|---|---|
| `dr status` | **Python CLI**, orchestrating direct commands | Read-only aggregation of a few independent probes (`drbdadm role` on each SAN, `pcs status` per cluster, VIP presence) — simple orchestration. |
| `dr bootstrap` | **Ansible playbook** (`site-pcmk-dr-bootstrap.yml`), thin CLI wrapper | Multi-host, stateful readying of the standby. |
| `dr failover` | **Ansible playbook** (`site-pcmk-dr-failover.yml`), thin CLI wrapper | Complex, ordered, cross-host, must be idempotent + recoverable. |

The thin wrappers mirror the existing `qm create` pattern (`_qm_playbook`):
render inventory → run the playbook with `-e` (direction + topology-derived
params) → propagate failure as `StepFailedError`. A `_dr_playbook` helper
factors the shared shape.

## 4. Topology-driven DR parameters

The cutover script hardcodes per-direction portal/VIP/nodes. Replace that with a
symmetric `dr` profile on the `pcmk_san_dr` setup in `lab/topology.yaml`:

```yaml
pcmk_san_dr:
  groups: [san_a, san_b, pcmk_a, pcmk_b]
  provision: ansible/site-pcmk-dr.yml
  secrets: [pcmk_hacluster_password]
  qm: { name: QMPCMK }
  dr:
    a: { san: san-a, cluster: pcmk_a, portal: 10.40.1.5, vip: 10.10.1.200, vip_ext: 10.60.0.10 }
    b: { san: san-b, cluster: pcmk_b, portal: 10.40.2.6, vip: 10.10.2.200, vip_ext: 10.60.0.20 }
```

A pure function — `dr_params(setup, from_site) -> { from_san, to_san,
from_cluster, to_cluster, to_portal, to_vip, to_vip_ext, to_nodes, qm }` —
derives the FROM/TO set for either direction from the two site blocks. It's the
single source the failover playbook reads via `-e`, and it's unit-tested both
directions like `scrape.py` / `dashboard.py`. (The site-A VIPs overlap
`pcmk_san_ha.qm`; an explicit symmetric `dr` block is preferred for clarity and
testability over deriving them.)

## 5. Direction auto-detection & recoverability

**Auto-detection.** "Which site is live" is a fact we read, not one the operator
asserts: `drbdadm role mqlun` on each SAN returns `Primary` (live) / `Secondary`
(standby). `dr failover` probes this to set FROM = the `Primary` site, TO = the
other; `dr status` renders it.

**The `--to` rule.** `failover` with no argument:

1. Probe DRBD role on both SANs.
2. If exactly one site is `Primary` → direction is unambiguous → proceed.
3. If it **cannot resolve** (an interrupted failover left neither side cleanly
   `Primary`, or both) → **fail loud** with the observed state and instruct the
   operator to re-run with `--to {a|b}`.
4. If `--to` is supplied → use it (the recovery override).

So the argument is demanded *only* in the one case the machine genuinely can't
know — which is exactly the recovery case.

**Recoverability.** A failover is an outage and may be interrupted. Every phase
is idempotent and checks live state before acting (demote only if `Primary`,
promote only if `Secondary`, mount only if unmounted, create-or-`enable`
`mq_group`, …), so **`dr failover` is re-runnable**: run `dr status`, see where it
stalled, re-run `failover` (with `--to` if the state is now ambiguous) and it
drives to completion from wherever it is.

## 6. The playbooks

**`site-pcmk-dr-bootstrap.yml`** (was `pcmk-dr-seed-peer.sh`): get the QM's
`addmqinf` command from the live site (`shell`: `dspmqinf` — no native module),
run it on the standby cluster, and install the **disabled** systemd unit via
`ansible.builtin.copy` + `ansible.builtin.systemd` (not the script's
`printf … && systemctl` blob).

**`site-pcmk-dr-failover.yml`** (was `pcmk-dr-cutover.sh` + `-force.sh`): the six
phases as ordered plays parameterised by the derived FROM/TO —

1. quiesce the live cluster (`pcs … standby`, wait for unmount),
2. demote DRBD on the FROM SAN (after confirming replication caught up),
3. promote DRBD on the TO SAN (or `--force` promote if peer dead),
4. export the LUN via LIO on the TO SAN,
5. attach storage on the TO cluster (iSCSI login),
6. start the `mq_group` on the TO cluster (create-or-enable).

Native modules where they exist (`systemd`, `mount`, `copy`, `file`); raw `shell`
only for `drbdadm` / `targetcli` / `pcs` (no native module), each with explicit
`failed_when`/`changed_when` — never `|| true`. No `community.general`, no
`uv run`.

## 7. Pre-flight & safety

A pre-flight assert play heads the failover playbook (fail loud, no interactive
prompt — it stays scriptable):

- FROM SAN is `Primary`, replication `Established`, `UpToDate/UpToDate` — never
  fail over mid-resync (data loss). **`--force`** relaxes this for a dead peer
  and instead asserts `role:Primary` *after* the force-promote.
- TO cluster nodes reachable.

No confirmation gate (so repeated validation runs stay automatable); safety comes
from the pre-flight asserts + `status` + recoverable re-runs.

## 8. Testing & validation

- **Pure-function tests** — `dr_params(setup, from_site)` for both directions; the
  `drbdadm role` → `from_site` live-detect parser (incl. the ambiguous case).
- **CLI tests (RecordingRunner)** — `status` probes the right hosts; `bootstrap` /
  `failover` render the inventory and invoke the right playbook with the derived
  `-e` args, as **bare `ansible-playbook`** (no `uv run`), with fail-loud
  propagation and the `--to`-required-when-ambiguous behaviour. Mirrors
  `test_cli_qm`.
- **Ansible** — `ansible-playbook --syntax-check` on the new playbooks (the #162
  lesson: `vrg-validate` does not syntax-check playbooks).
- **Live** — validate `status` / `bootstrap` / `failover` / failback against the
  running `pcmk_san_dr` cluster (human-operated).
- All under `vrg-validate`; 100% branch coverage on the Python.

## 9. Scope boundary

**In:** `dr status` / `dr bootstrap` / `dr failover` (+ `--force`, + `--to` when
ambiguous); the topology `dr` profile + `dr_params` derivation + live-detect; the
`site-pcmk-dr-bootstrap.yml` and `site-pcmk-dr-failover.yml` playbooks;
pre-flight; tests. **Retire** `pcmk-dr-seed-peer.sh`, `pcmk-dr-cutover.sh`,
`pcmk-dr-force.sh`.

**Deferred:** `mqlab dr validate` — the resilience/RPO validation *experiments*
(mid-flow HA+DR, RPO semantics) live in #149's territory and warrant their own
spec. This design only makes the lifecycle *driveable*; measuring it comes next.

## 10. Open questions

- **`status` output shape** — a Rich table (site / DRBD role / mq_group / VIPs)
  vs. plain lines. Lean: a compact table; finalise in implementation.
- **`bootstrap` idempotency on an already-bootstrapped peer** — re-running should
  be a clean no-op (addmqinf already present, unit already installed+disabled);
  confirm the guards.
- **Does `dr` belong only on `pcmk_san_dr`, or also a future `rdqm_dr`?** This
  spec targets the Pacemaker/SAN arm; RDQM has its own `rdqmdr` verbs. Keep `dr`
  pcmk-scoped for now; revisit if RDQM-DR is folded into the CLI.
