# RDQM DR Cutover Robustness — Findings

**Date:** 2026-07-31
**Issue:** #294 (epic logical-minds-foundry/.github#104)
**Status:** cutover script hardened for the three #288-drill findings; logic validated by
review + shellcheck + unit tests. A live DR-drill re-validation is a natural follow-on (not
run here — the shared rdqm-rhel arm was kept healthy).

Follow-up to `2026-06-19-rdqm-hadr-automation-findings.md` (#288), which proved the RDQM HADR
build path + the `rdqmdr` role-flip but surfaced three cutover gaps. This hardens
`lab/scripts/rdqm-dr-cutover.sh` against all three.

## Finding 1 — run `rdqmdr` on the current HA primary, not a hardcoded node-1

RDQM floats the QM's HA role across a site's three group nodes; in the #288 drill site-B's QM
landed on **rdqm-b2**, so `rdqmdr -m QMRDQM -s` issued on the hardcoded rdqm-b1 failed
`AMQ3705E: not issued on the HA primary node` and failback exited 2.

**Fix.** A new `ha_primary()` resolver queries any reachable group node and reads the local
block's `HA current location` field from `rdqmstatus` — reported as `This node` on the primary
itself, or the primary's node name on a secondary — so one query resolves the primary. The
demote (`-s`) and promote (`-p`) now target the resolved `FROM_PRIMARY` / `TO_PRIMARY`. This
works on the DR-secondary site too (its QM is `Ended`, but the HA primary is still reported).

The post-cutover verify/poll had the same bug (it only ever looked at node-1). The script now
**re-resolves the TO-site HA primary after the promote** and points the settle-wait + final
`rdqmstatus` at it, so the verify follows the QM wherever HA placed or relocated it.

## Finding 2 — confirm DR is in-sync before cutting

The proven paved path is "confirm replication caught up, *then* cut." The old script cut
immediately, risking activation of an incomplete recovery copy.

**Fix.** A new `confirm_dr_in_sync()` gate polls the live site's HA primary for `DR status`
`Normal` before the demote, bounded by `DR_SYNC_TIMEOUT` (default 300 s, env-overridable). Any
non-`Normal` state (`Synchronization in progress`, `Remote unavailable`, `Partitioned`,
`Inactive`, ...) keeps it waiting; on timeout it fails loud and refuses to cut.

## Finding 3 — post-promote HA bounce / Pacemaker "HA blocked location: All nodes"

Handled as: **document as a TCG-only timing flake + add a defensive bounded settle-wait** —
*not* by tuning Pacemaker op timeouts. Reasoning:

- **RDQM does not expose the knob.** RDQM hides Pacemaker behind `rdqmadm`; the lab's
  `ansible/roles/rdqm-ha/templates/rdqm.ini.j2` sets no start/monitor/stop timeouts, and RDQM
  offers no supported path to tune the QM resource's op timeouts from the lab. "Tuning the
  thresholds" is therefore not cleanly available, and forcing it would be over-engineering.
- **The capability is proven.** Phase-C cut A→B in ~69 s and failed back in ~104 s, both at
  RPO 0 (`2026-06-06-phase-c-rdqm-findings.md`). In the #288 event the QM logged only warnings
  plus a controlled end — no data fault — and the node had free RAM. It is an emulation-slowness
  race in which the QM start trips RDQM's start/monitor timing and Pacemaker blocks the HA
  location; on real hardware this race does not occur.
- **The proven recovery is a re-flip, not an `rdqmadm --resume`.** #288 recovered by
  re-issuing `rdqmdr -s` on the *real* HA primary and `rdqmdr -p` on the peer — service restored
  without a rebuild. Finding 1's discovery now makes that re-flip trivially correct to run, so
  the script does **not** auto-invoke an unproven remediation.

**Fix.** A new `wait_ha_settle()` replaces the brittle single-shot verify: it polls the new
primary until the QM is `Running` with `HA blocked location` `None`, bounded by
`HA_SETTLE_TIMEOUT` (default 180 s, env-overridable). If the TCG blocked-location artifact
appears, it surfaces the condition loudly with the proven manual recovery and continues rather
than emitting a confusing mid-bounce status.

## Validation

- `shellcheck` (via `vrg-validate`) — clean.
- `tests/test_rdqm_dr_cutover.py` — `bash -n`, strict-mode, and the three findings' contracts,
  plus behavioral tests of the `rdqm_field` rdqmstatus parser against real captures from the
  live arm (primary "This node" vs. secondary named-node; `HA current location` not confused
  with `HA preferred location`; `DR status`; blocked-location / QM-status reads).
- `rdqmstatus` grammar grounded read-only against the live rdqm-rhel arm (RDQMAPP, HA primary
  rdqm-a1, DR status Normal).

**Not done here (deliberately):** a live DR cutover/failback drill (disruptive to the shared
arm). That live re-validation of the hardened script is the natural follow-on.

## Deferred (the #294 "also worth doing" items)

- A `mqlab dr cutover|failback <setup>` verb so the script runs inside the venv (today it needs
  `uv run` for ansible on PATH). The `src/mqlab/dr/` package is currently the DR
  measurement/validation framework, not a cutover verb — a thin wrapper is worthwhile but out
  of scope for this hardening pass.
- An RPO-0 message-survival assertion (seed persistent msg → cutover → retrieve at peer),
  reproducing the Phase-C proof as an automated check — needs a live drill to exercise.
