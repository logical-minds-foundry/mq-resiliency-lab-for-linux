# IBM MQ 9.4.5 → 10.0 Native HA CRR Upgrade — Execution Evidence

**Date:** 2026-09-14
**Issues:** #1074 (execute the rolling upgrade) and #1076 (cold-rebuild gate),
epic logical-minds-foundry/.github#219 — MQ 9.4.5 → 10.0 upgrade + version
centralization.
**Task:** Workstream C evidence bookend. Commit the executed-upgrade result that
otherwise lived only in the #1074 / #1076 issue comments, so the runbook
([`nativeha-mq-upgrade-runbook.md`](../reference/nativeha-mq-upgrade-runbook.md))
carries a committed, client-anonymized proof that the procedure was run and the
MQ acceptance criteria met.

**Status:** DONE — both gates scored SUCCESS on their MQ acceptance criteria.
All identifiers below are the lab's own generic names; no client identifiers,
host names, addresses, or secrets appear here.

---

## Environment

- Cloud x86 host (GCP), RHEL 9.6 guests under libvirt `qemu:///system`.
- Queue manager `NHARAPP`, Native HA CRR "3+3": a Live group
  (`nha-rhel-a1/a2/a3`) asynchronously replicated to a Recovery group
  (`nha-rhel-b1/b2/b3`), DR enabled.
- Procedure followed:
  [`docs/reference/nativeha-mq-upgrade-runbook.md`](../reference/nativeha-mq-upgrade-runbook.md),
  **Sequence A** (maintenance-window outage, Recovery-group-first).

## 1. Rolling upgrade 9.4.5.0 → 10.0.0.0 (#1074) — SUCCESS

Executed against the full RHEL arm and verified. The lab was cold-bootstrapped
at the then-current `lab/mq-version` pin (9.4.5.0) with the 10.0.0.0 developer
media staged in `build/cache/mq`, then rolled per Sequence A.

| Checkpoint | Evidence |
|---|---|
| Baseline (9.4.5.0) | Both groups `QUORUM(3/3)` / `GRSTATUS(Normal)`, Recovery `CONNGRP(yes)`, `dspmqver 9.4.5.0` ×6 |
| Recovery group → 10.0 first | b1/b2/b3 `dspmqver 10.0.0.0`, group `QUORUM(3/3) HASTATUS(Normal)` |
| Mid-upgrade invariant | Live `GRPVER(9.4.5.0)` serving, Recovery `GRPVER(10.0.0.0) CONNGRP(yes)` — the §2.1 Recovery ≥ Live invariant held; CRR healthy across the version boundary |
| Live group → 10.0 | a1/a2/a3 `dspmqver 10.0.0.0`; both groups `GRPVER(10.0.0.0) GRSTATUS(Normal)`, Recovery `CONNGRP(yes)` — upgrade "complete" per runbook §2.1 |
| DR cutover A → B (10.0) | site B `GRPROLE(Live)`, b2 Active `QUORUM(3/3)`; site A `GRPROLE(Recovery)`; both `Normal` / `CONNGRP(yes)` |
| DR failback B → A (10.0) | site A `GRPROLE(Live)`, a1 Active `QUORUM(3/3)`; site B `GRPROLE(Recovery)` — original roles restored |
| Post-upgrade sanity | put→get round-trip on the active 10.0 QM: sanity message retrieved, `CURDEPTH` 1→0 |

Back-out backups (9.4.5 queue-manager data) were taken on all six nodes before
each group's first 10.0 `strmqm` — the runbook §4.1 point of no return. The DR
round trip (cutover then failback) was driven by the Ansible playbook
`ansible/site-nativeha-switchover.yml` (`-e target_live=b` to cut over, then
`-e target_live=a` to fail back), the mechanism documented in runbook §5. Full
per-checkpoint `dspmq -o nativeha -g/-x` output was captured to
`build/temp/mq10-upgrade-evidence/` during the run.

**Findings raised during execution and applied to the docs in this epic:**

1. **Runbook §5 driver correction.** The switchover round trip is driven by the
   `site-nativeha-switchover.yml` playbook, **not** by `mqlab dr cutover` /
   `mqlab dr failback` — those verbs are rdqm-only today (native-HA wrappers
   unbuilt, automation epic `vergil-project/.github#38`). Corrected in the
   runbook §5 lab-arm note.
2. **Runbook §3.1 quiesce enhancement.** The mqweb server must be stopped
   (`endmqweb` / `systemctl stop mqweb`) before the package upgrade: the 10.0
   RPM `%prein` scriptlet aborts while `/opt/mqm` is in use, so ending the queue
   manager alone is insufficient. Added to the runbook §3.1 checklist.

## 2. Cold-rebuild gate at the 10.0 pin (#1076) — SUCCESS

After the `lab/mq-version` pin was rebaselined to `10.0.0.0`, the RHEL Native HA
CRR arm was rebuilt from zero to prove the pin → box → one-pass chain, with the
full 10.0 media set staged in `build/cache/mq`.

| Criterion | Evidence |
|---|---|
| All six instances on 10.0 | `dspmqver -b -f 2` = `10.0.0.0` on all six nodes (a1–a3, b1–b3) |
| Both groups quorate | Live `QUORUM(3/3)` (INSYNC / HASTATUS Normal); Recovery `QUORUM(3/3)` (b1 Leader, b2/b3 Replica, INSYNC / HASTATUS Normal) |
| CRR healthy | `dspmq -o nativeha -g`: both `GRPVER(10.0.0.0) GRSTATUS(Normal)`, Recovery `CONNGRP(yes)` |
| Provisioned from the pin, no manual MQ fix-ups | `nativeha-rhel provision ✓` one-pass; nodes came up 10.0 with no per-node MQ intervention |

This run also validated the box-pin fix (#1087/#1088) end-to-end against the real
box cache: at the 10.0 pin the MQ-bearing boxes (`mq-nativeha-rhel9`,
`mq-ubuntu2404`) flipped to BUILD while the commons boxes stayed REUSE — the
single-source pin genuinely rebases the whole lab.

## 3. Caveat tracked separately

The full bootstrap did **not** reach `observe ✓` unattended: the `observe` phase
(Grafana dashboard-deploy / image-renderer plus the resident guests) reproducibly
OOM-killed the orchestrator on this 31 GiB host. The MQ layer was unaffected — it
provisions before `observe`. This is a host-capacity / observability-footprint
issue independent of the MQ 10.0 migration, tracked in #1089 (resize the host or
gate the Grafana renderer); the cold-rebuild gate was scored on its stated MQ
criteria per maintainer decision.

## Outcome

The IBM MQ 9.4.5.0 → 10.0.0.0 Native HA CRR rolling upgrade was executed and
verified on the RHEL arm — both groups quorate on 10.0 with CRR connected, DR
cutover/failback proven, functional sanity passed — and a cold rebuild from the
rebaselined 10.0 pin brings the whole arm up on 10.0 in one pass. The runbook is
proven against a real execution, with the two doc corrections above folded back
in.
