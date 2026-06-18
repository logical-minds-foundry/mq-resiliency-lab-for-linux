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

## Distributed mesh (`QMNATIVE` ↔ `QMDTCC` + app) — ✅

`site-nativeha-distributed.yml` imports the shared, substrate-free DTCC/app layer
(identical to the rdqm/pcmk arms) over the Native HA substrate. **No floating VIP:**
- QMDTCC's their-side channels reach `QMNATIVE` via a CONNAME list of the three
  `nha_rhel_a` **net-ext** IPs (mirrors RDQM's per-node CONNAME approach);
- the app reaches `QMNATIVE` via a CONNAME list of the three **net-data-a** IPs,
  `MQCNO_RECONNECT` — connecting to whichever instance is active.

Our-side MQSC (`APP.SVRCONN` + `DTCC.REQUEST` QREMOTE/XMITQ + SDR/RCVR) applied on
the active instance and **replicated to all three by raft**. The `QMNATIVE.QMDTCC`
SDR channel runs.

**End-to-end proof:** `app_requester.py --qm QMNATIVE --conn <3-instance list>`
→ **5/5 round-trips OK** (`req-NNNN` → `QMDTCC` responder → `REPLY:req-NNNN` back).
Same app contract as the other arms, new substrate.

## Carry-forward
- Cold-rebuild acceptance gate — Task 5.
- Phase 3: site B (`nha_rhel_b`) + CRR/DR on this same consolidated setup, real TLS
  (GSKit extraction first — `lab-gotchas.md`).
