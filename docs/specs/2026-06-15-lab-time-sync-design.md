# Lab Time Synchronization — Design

- **Date:** 2026-06-15
- **Status:** Design — brainstormed + pushback-reviewed 2026-06-15
- **Issue:** #186
- **Depends on:** **`vergil-project/vergil-tooling#1664`** — the *time authority* (the
  hypervisor running chrony with `makestep`, serving NTP on its libvirt host-only
  network) is a **Vergil base-VM feature**, not built here. This spec is **guest-side
  only**; it is gated on that feature landing.
- **Relationship:** prerequisite-quality infrastructure. Cross-node log correlation,
  event-timeline reconstruction, the day-long HA/DR loop, and the #177 cluster-cockpit
  STALE signal depend on synchronized clocks. **#177 is paused until this lands.**

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. Architecture — chrony clients, Vergil-provided authority](#3-architecture--chrony-clients-vergil-provided-authority)
- [4. Components](#4-components)
- [5. Provisioning order](#5-provisioning-order)
- [6. Monitoring](#6-monitoring)
- [7. Manual command](#7-manual-command)
- [8. Cross-cutting concerns](#8-cross-cutting-concerns)
- [9. Open questions](#9-open-questions)
- [10. Acceptance criteria](#10-acceptance-criteria)

---

## 1. Problem & motivation

Lab VM clocks drift apart. A laptop suspend left the observability box **~65 minutes
ahead** of the cluster nodes, and stock Ubuntu `systemd-timesyncd` did **not** recover:
it slow-slews and refuses to *step* a large offset, so a suspend-induced jump sits
uncorrected indefinitely. The nodes reported `NTPSynchronized=yes` the whole time —
the sync daemon believed it was fine while the fleet was over an hour apart.

Unsynchronized clocks are corrosive to the lab's purpose: **log correlation and
event-timeline reconstruction across nodes** is how outages are investigated and how
the day-long HA/DR loop builds its narrative. If `pcmk-a1` and the obs box disagree by
an hour, every cross-node timeline is wrong. Today there is **no time-sync
infrastructure** in the lab — only the OS defaults.

## 2. Goals & non-goals

**Goals**

- Every **guest** holds a clock consistent with the rest of the fleet — internally
  (agreement across nodes matters more than absolute accuracy for log correlation).
- Guests **recover automatically from a suspend/resume jump** (step, not slow-slew) and
  stay disciplined across **multi-hour** runs.
- A clock **step is safe under a running cluster** — corosync/DRBD must not destabilize
  when the wall clock is corrected mid-run (see §8).
- Works **offline** — no dependence on each guest reaching the internet (the authority
  is internal).
- **Drift is observable** — a per-node metric so skew is visible.
- A **manual force-sync + status** command for on-demand correction and verification.
- Everything provisioned as code; proven by a cold rebuild.

**Non-goals**

- **The time authority itself.** The hypervisor running chrony with `makestep` and
  serving NTP on its libvirt host-only network is a **Vergil base-VM feature**
  (`vergil-tooling#1664`), not built in this repo. The host's own clock reliability +
  serving its subnets is platform-general; solving it once in the base VM avoids a
  bespoke per-repo hack against the "don't hand-customize the VM" model. This spec
  **consumes** that authority.
- **Incremental provisioning onto the already-running lab.** Applying time-sync on top
  of a live, skewed cluster is a bespoke one-time scenario we will never have in
  practice; not supported or tested. The only supported path is base bring-up on a
  cold build.
- **Absolute-time accuracy / stratum-1 rigor**, and **securing NTP** (NTS/auth) — out
  of scope, consistent with the lab's posture.

## 3. Architecture — chrony clients, Vergil-provided authority

**The authority (external, `vergil-tooling#1664`):** the hypervisor (the Vergil VM)
runs chrony as the lab's NTP server, reachable by every guest at **`10.50.0.1`** on the
host-only `net-mgmt` plane, with `makestep` (self-heals its own clock after a suspend)
and a `local stratum` fallback (valid even fully offline). This repo does **not**
configure it; it relies on the base VM providing it.

**This repo — the clients (every guest):** each guest runs **chrony** pointed at
`10.50.0.1`, replacing Ubuntu's `systemd-timesyncd` and configuring the Alma/RHEL
nodes' native chrony. The load-bearing setting is **`makestep 1 -1`**: chrony *steps*
the clock immediately for any offset over 1 second, at any time. A suspend jump is
corrected on the next poll — seconds, not the hours timesyncd's slew would take. This
is the direct fix for the failure that motivated the work.

Why chrony over timesyncd: chrony is the standard answer for hosts that suspend/resume
or see clock jumps — it steps aggressively and re-converges fast, exactly where
timesyncd's conservative slew fails.

## 4. Components

| Component | Responsibility | Owner |
|---|---|---|
| **Time authority** | Hypervisor chrony server (`makestep`, serve `10.50.0.0/24`, `local stratum` fallback) | **Vergil base VM** (`vergil-tooling#1664`) — *dependency, not built here* |
| **`time-sync` role (guests)** | Install + enable chrony; disable/remove `systemd-timesyncd` (Ubuntu); render `chrony.conf` (server `10.50.0.1`, `makestep 1 -1`). OS-family aware (Ubuntu + Alma/RHEL). | this repo |
| **Monitoring** | node_exporter's **`timex`** collector → `node_timex_sync_status` + `node_timex_offset_seconds` per node (§6) | this repo (collector toggle) |
| **`mqlab time` verbs** | `mqlab time status` (per-node offset + sync state) and `mqlab time sync` (force `chronyc makestep` fleet-wide) (§7) | this repo |

The guest role is one focused unit; monitoring is a collector toggle; the CLI verbs are
a thin `chronyc`/ansible wrapper with a tested parse function. Each is independently
testable.

## 5. Provisioning order

Order is load-bearing — the reason guest sync is base infrastructure, not an add-on:

1. **Authority exists at boot** — provided by the Vergil base VM (`#1664`); present
   before any guest comes up. (Not this repo's step.)
2. **Guests sync** via the `time-sync` role, run **early** — before cluster formation.
   This needs a base/common play applied to all guests (none exists today; this work
   introduces one, run first by the composite setup playbooks).
3. **Cluster / MQ roles** form afterward (corosync, DRBD, Pacemaker, queue managers) on
   already-synchronized clocks.

Because step 2 precedes cluster formation on a cold build, the clock is correct before
corosync/DRBD ever start — so there is no "step a live cluster's clock at provision
time" hazard (§2 non-goals). Steps *during* a run are addressed in §8.

## 6. Monitoring

node_exporter exposes clock health through its **`timex`** collector — on most builds
enabled by default, so the metrics may already flow; the role confirms it is on (and
enables it in the `node-exporter` role if not — confirm in §9). Per node:

- `node_timex_sync_status` — 1 when synchronized, 0 when not.
- `node_timex_offset_seconds` — current estimated offset.

These ride the existing `node` scrape job (no new job) and make drift **visible** in
Prometheus/Grafana. The lab has **no Alertmanager**, so "alerting" here means a
queryable/visible signal (a Grafana panel, and a ready signal for a future #177 cockpit
clock-skew tile), not a firing alert — until/unless an Alertmanager is added.

## 7. Manual command

The standing chrony config does the continuous work; these verbs are the **force-now +
verify** tools:

- **`mqlab time status`** — query each node's chrony (`chronyc tracking` / `-c`) and
  report per-node offset + sync state in one view. The `chronyc` parse is a pure,
  unit-tested function.
- **`mqlab time sync`** — run `chronyc makestep` fleet-wide to force an immediate
  correction rather than waiting for the next poll.

`time` is a new top-level verb namespace alongside `net` / `obs` / `qm` / `vm`.

## 8. Cross-cutting concerns

- **A clock step is safe under a running cluster.** `makestep` will step a *live*
  cluster node's wall clock on resume from a mid-run suspend. This is safe because
  **corosync's token/membership timers and DRBD's timers use `CLOCK_MONOTONIC`**, which
  is unaffected by wall-clock steps — a step shifts log timestamps (exactly what we want
  corrected) without disturbing the cluster protocol. This rationale is **verified by an
  acceptance test** (§10.3), not assumed.
- **chrony installability.** Ubuntu guests install chrony via apt (they have outbound
  NAT during provisioning); Alma/RHEL ship chrony as the native time daemon. Confirm
  availability in the (possibly offline) provisioning path — §9.
- **Offline-first / internally consistent.** With the authority's `local stratum`
  fallback, the fleet stays internally consistent even with no internet; absolute
  accuracy is best-effort.
- **No new Prometheus job / no new secrets.** Monitoring rides the existing `node` job;
  NTP is unauthenticated by design.
- **Testing.** Pure functions — the `chronyc` status parse, any chrony.conf rendering
  helper — unit-tested to the repo's 100% branch bar. Role validated by the cold-rebuild
  run (§10). `vrg-container-run -- vrg-validate` is the only gate.
- **Fail-loud.** A guest that cannot reach the authority must surface
  (`node_timex_sync_status == 0`), never silently free-run unnoticed.

## 9. Open questions

- **Base play placement.** Where the new all-guests base play is imported by the
  composite setups (`site-distributed.yml` / `site-pcmk.yml` / `site-rdqm.yml`) so it
  runs before every cluster role, with least duplication. First task of the plan.
- **chrony installability offline.** Confirm chrony installs in the provisioning path on
  Ubuntu guests (apt) and is present on Alma/RHEL; handle the offline case if it bites.
- **`timex` collector default state** on the lab's node_exporter build — confirm on;
  enable explicitly in the `node-exporter` role if not.
- **`mqlab time` namespace vs. fold-in** — cosmetic; confirm during the plan.
- **Vergil dependency readiness** — this repo cannot fully test until `#1664` provides
  the authority. Track the gating explicitly (§10).

## 10. Acceptance criteria

1. With the Vergil authority present (`#1664`), a **complete cold lab rebuild** comes up
   with every **guest** time-synchronized — `mqlab time status` shows sub-second offsets
   fleet-wide.
2. An **induced skew** — a deliberate `date`-step on a guest, or a real suspend/resume —
   **self-corrects within a poll interval** (chrony `makestep`), no manual action.
3. **A clock step under a running cluster does not destabilize it** — step a cluster
   guest's wall clock (e.g. back an hour) on a healthy running cluster and confirm
   corosync/DRBD/Pacemaker stay healthy (monotonic-timer rationale, §8).
4. `mqlab time sync` forces an immediate fleet-wide correction; `mqlab time status`
   reports per-node offset + sync state.
5. `node_timex_sync_status` / `node_timex_offset_seconds` are present in Prometheus for
   every node (existing `node` job); a node that loses the authority reads
   `sync_status == 0` (fail-loud).
6. Reproducible from a **cold rebuild**, passes `vrg-validate`. Iteration may rebuild
   the dev VM and individual guests, but the proof is always a full cold build;
   incremental application onto a running lab is **not** supported or tested.
7. Corosync/DRBD/Pacemaker form on already-synchronized clocks (time-sync ordered before
   cluster roles).

> **Gating:** criteria 1–3 require the Vergil base-VM authority (`vergil-tooling#1664`).
> Until it lands, this repo's guest-side role/verbs/monitoring can be built and
> unit-tested, but the end-to-end cold-rebuild acceptance — and the resumption of
> #177 — wait on that dependency.
