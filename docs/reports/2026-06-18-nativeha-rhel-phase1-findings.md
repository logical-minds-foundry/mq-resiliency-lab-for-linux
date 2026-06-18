# Native HA on RHEL — Phase 1 findings

> **Issue:** #246 (Phase 1). **Date:** 2026-06-18. **Arm:** `nativeha-rhel`.
> **Substrate:** three RHEL 9.6 x86-64 TCG guests (`nha-rhel-a1..3`), MQ 9.4.5
> Advanced for Developers, base install (no RDQM). Functional correctness only
> (TCG — no timing claims). QM `QMNATIVE`, replication on `net-hb-a` (172.16.1.7x).

## HA group formation — ✅

`QMNATIVE` forms `QUORUM(3/3)` via the production `mq-nativeha` role (shared
formation + `install-RedHat.yml`): one Active + two Replica, all `INSYNC(yes)`,
`HASTATUS(Normal)`, replication on the **dedicated `net-hb-a` NIC** (172.16.1.71-73).

## §3.1 fault suite (`lab/scripts/nativeha-fault-suite.sh`)

| Drill | Action | Observed | Verdict |
|---|---|---|---|
| **Hard power-off active** | `virsh destroy` the Active node | a survivor re-elected Active within seconds; **`QUORUM(2/3)` held, QM stayed up**; downed node rejoined as Replica and resynced (`BACKLOG`→0, `INSYNC`) when restarted → `QUORUM(3/3)` | ✅ automatic failover + rejoin |
| **Planned failover** | `systemctl stop mqmonitor@QMNATIVE` (the `qm-down` verb) on the Active | another instance took Active, `QUORUM(2/3)`, QM available | ✅ |
| **Planned failback** | `systemctl start mqmonitor@QMNATIVE` (the `qm-up` verb) | stopped instance rejoined → `QUORUM(3/3)` | ✅ |
| **Sever replication NIC** | `domif-setlink … down` on the Active's `net-hb-a` | survivors (majority) formed `QUORUM(2/3)` + one new Active; **isolated node went `QUORUM(0/3)`, `ROLE(Unknown)`, refused to be Active** → no split-brain; healed on restore | ✅ no split-brain |

**No auto-failback:** after a failover the new Active *keeps* the role (no
disruptive move-back) — good HA hygiene, contrast the Pacemaker arm's
`resource-stickiness` tuning (lab-gotchas) needed to get the same property.

## Apples-to-apples ledger (vs RDQM / Pacemaker)

- **Turnkey, in-QM:** quorum, election, replication, and no-split-brain are all
  **the queue manager's own** (raft) — no DRBD, no Pacemaker/Corosync, no STONITH,
  no shared storage, no VIP/IPaddr2 resource. The whole HA substrate the other
  arms hand-build is *absent*.
- **Lifecycle:** `mqmonitor@` systemd unit (not `endmqm`/`pcs`).
- **Shared-nothing:** no SAN/LUN to sever — the "sever shared storage" drill is
  n/a (itself a finding vs the SAN/DRBD arms).
- **OS-adapter thinness:** install is the only OS-specific surface
  (`install-RedHat.yml`); formation is shared — Phase 2 (Ubuntu) measures how thin.

## Carry-forward
- Distributed mesh (`QMNATIVE` ↔ `QMDTCC` + app) — Task 4.
- Cold-rebuild acceptance gate — Task 5.
- Native HA client connectivity is a **multi-instance CONNAME list** (no VIP) —
  built from `nha_rhel_*` data IPs in the distributed content (Task 4).
