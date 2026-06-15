# Lab Time Synchronization — Design

- **Date:** 2026-06-15
- **Status:** Design — brainstormed 2026-06-15
- **Issue:** #186
- **Relationship:** prerequisite-quality infrastructure. Cross-node log correlation,
  event-timeline reconstruction, the day-long HA/DR loop, and the #177 cluster-cockpit
  STALE signal all depend on synchronized clocks. #177 is paused for this.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. Architecture — chrony, hypervisor authority](#3-architecture--chrony-hypervisor-authority)
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
an hour, every cross-node timeline is wrong. Corosync and DRBD are also clock-sensitive.
Today there is **no time-sync infrastructure** in the lab at all — only the OS defaults.

## 2. Goals & non-goals

**Goals**

- Every node — all guests **and** the hypervisor — holds a clock consistent with the
  rest of the fleet, **internally** (agreement across nodes matters more than absolute
  accuracy for log correlation).
- The fleet **recovers automatically from a suspend/resume jump** (steps, not slow-slews)
  and stays disciplined across **multi-hour** runs.
- Works **offline** — no dependence on each guest reaching the internet.
- **Drift is observable** — a metric per node so skew is visible and alertable.
- A **manual force-sync + status** command for on-demand correction and verification.
- Everything provisioned as code; proven by a cold rebuild.

**Non-goals**

- **Incremental provisioning onto the already-running lab.** Applying time-sync on top
  of a live, skewed cluster (stepping a running corosync/DRBD's clock backward an hour)
  is a bespoke one-time scenario we will never have in practice. We do not support or
  test it; fighting its edge cases is wasted effort. The **only** supported path is
  provisioning as part of base bring-up, on a cold build.
- **Absolute-time accuracy / stratum-1 rigor.** Internal consistency is the objective;
  the hypervisor's upstream is best-effort.
- **Securing NTP** (authenticated NTP / NTS). Out of scope, like the rest of the lab's
  security posture.

## 3. Architecture — chrony, hypervisor authority

**chrony everywhere, with the hypervisor as the lab's internal NTP authority.**

- **Authority — the hypervisor.** The Vergil VM (the libvirt host) runs **chrony as an
  NTP server**, reachable by every guest at **`10.50.0.1`** on the host-only `net-mgmt`
  plane (every node already carries a `net-mgmt` NIC). It disciplines itself from a
  public pool **when reachable**, and declares **`local stratum 10`** so it remains a
  valid time source even fully offline. The lab therefore stays *internally consistent*
  regardless of internet — which is what log correlation needs. This mirrors real
  datacenter internal-NTP.
- **Clients — every guest.** Each guest runs **chrony** pointed at `10.50.0.1`,
  replacing Ubuntu's `systemd-timesyncd` and configuring the Alma/RHEL nodes' native
  chrony. The load-bearing setting is **`makestep 1 -1`**: chrony *steps* the clock
  immediately for any offset over 1 second, at any time (not just at startup). A
  suspend jump is corrected on the next poll — seconds, not the hours timesyncd's slew
  would take. This single behaviour is the direct fix for the failure that motivated
  the work.

Why chrony over timesyncd: chrony is the standard answer for hosts that suspend/resume
or otherwise see clock jumps — it steps aggressively and re-converges fast, exactly
where timesyncd's conservative slew fails.

## 4. Components

| Component | Responsibility |
|---|---|
| **`time-sync` role (guests)** | Install + enable chrony; disable/remove `systemd-timesyncd` (Ubuntu); render `chrony.conf` (server `10.50.0.1`, `makestep 1 -1`). OS-family aware (Ubuntu + Alma/RHEL). |
| **Hypervisor chrony-server** | In the host-side play (`host-obs.yml` lineage, `connection: local`): chrony server config allowing `10.50.0.0/24`, public upstream + `local stratum 10` fallback. |
| **Monitoring** | node_exporter's **`timex`** collector → `node_timex_sync_status` + `node_timex_offset_seconds` per node (§6). |
| **`mqlab time` verbs** | `mqlab time status` (per-node offset + sync state) and `mqlab time sync` (force `chronyc makestep` fleet-wide) (§7). |

The guest role is one focused unit (configure-and-enable chrony); the server config is a
sibling on the host play; monitoring is a collector toggle; the CLI verbs are a thin
ansible/`chronyc` wrapper with a tested parse function. Each is independently testable.

## 5. Provisioning order

Order is load-bearing and is the reason this is base infrastructure, not an add-on:

1. **Hypervisor chrony server** comes up first (host-side play) — the authority must
   exist before anyone syncs to it.
2. **Guests sync** via the `time-sync` role, run **early** — before cluster formation.
   This needs a base/common play applied to all guests (none exists today; this work
   introduces one, run first by the composite setup playbooks).
3. **Cluster / MQ roles** form afterward (corosync, DRBD, Pacemaker, queue managers) on
   already-synchronized clocks.

Because step 2 precedes cluster formation on a cold build, the clock is correct before
corosync/DRBD ever start — so there is no "step a live cluster's clock" hazard to manage
(see §2 non-goals).

## 6. Monitoring

node_exporter exposes clock health through its **`timex`** collector — on most builds
it is enabled by default, so the metrics may already flow; the role confirms it is on.
Per node we get:

- `node_timex_sync_status` — 1 when the clock is synchronized, 0 when not.
- `node_timex_offset_seconds` — the current estimated offset.

These ride the existing `node` scrape job (no new job), make drift **visible and
alertable** in Prometheus, and give the #177 cockpit a ready signal for a future
per-node **clock-skew** tile. A recording rule / alert on
`abs(node_timex_offset_seconds) > <threshold>` or `node_timex_sync_status == 0` is the
loud signal that the discipline has failed.

## 7. Manual command

The standing chrony config does the continuous work; these verbs are the **force-now +
verify** tools:

- **`mqlab time status`** — query each node's chrony (`chronyc tracking` / `-c`) and
  report per-node offset + sync state in one view. The parse of `chronyc` output is a
  pure, unit-tested function.
- **`mqlab time sync`** — run `chronyc makestep` fleet-wide to force an immediate
  correction rather than waiting for the next poll.

`time` is a new top-level verb namespace alongside `net` / `obs` / `qm` / `vm`.

## 8. Cross-cutting concerns

- **Offline-first / internally consistent.** The `local stratum 10` fallback means the
  authority is always valid; absolute accuracy is best-effort, internal agreement is
  guaranteed.
- **No new Prometheus job / no new secrets.** Monitoring rides the existing `node` job;
  NTP is unauthenticated by design (§2).
- **Testing.** Pure functions — the `chronyc` status parse, any chrony.conf rendering
  helper — unit-tested to the repo's 100% branch bar. Role + server config validated by
  the cold-rebuild run (§10). `vrg-container-run -- vrg-validate` is the only gate.
- **Idempotence + fail-loud.** The role is declarative; a guest that cannot reach the
  authority must surface (chrony unreachable → `node_timex_sync_status == 0`, the §6
  loud signal), never silently free-run unnoticed.

## 9. Open questions

- **Hypervisor upstream when offline at first boot.** Confirm chrony serves correctly
  with only the `local stratum` directive (no reachable upstream) so a fully-isolated
  cold build still converges the fleet internally.
- **`timex` collector default state** on the lab's node_exporter build — confirm it is
  on; enable explicitly in the `node-exporter` role if not.
- **Base play placement.** Exactly where the new all-guests base play is imported by the
  composite setups (`site-distributed.yml` / `site-pcmk.yml` / `site-rdqm.yml`) so it
  runs before every cluster role, with the least duplication.
- **`mqlab time` namespace vs. fold-in.** `time` as a new top-level verb vs. nesting
  under an existing namespace — cosmetic; confirm during the plan.

## 10. Acceptance criteria

1. A **complete cold lab rebuild** (nuked + rebuilt from scratch) comes up with the
   whole fleet **time-synchronized** — `mqlab time status` shows sub-second offsets
   across all guests + the hypervisor.
2. An **induced skew** — a deliberate `date`-step on a guest, or a real suspend/resume —
   **self-corrects within a poll interval** (chrony `makestep`), with no manual action.
3. `mqlab time sync` forces an immediate fleet-wide correction; `mqlab time status`
   reports per-node offset + sync state.
4. `node_timex_sync_status` / `node_timex_offset_seconds` are present in Prometheus for
   every node (existing `node` job); a node that loses the authority reads
   `sync_status == 0` (fail-loud), never a silent free-run.
5. The whole layer is reproducible from a **cold rebuild** and passes `vrg-validate`.
   Iteration may rebuild the dev VM and individual guests, but the proof is always a
   full cold build; incremental application onto a running lab is explicitly **not**
   supported or tested.
6. Corosync/DRBD/Pacemaker form on already-synchronized clocks (time-sync ordered before
   cluster roles), so the clusters are never built on skewed time.
