# IBM MQ Native HA CRR — 9.4.5 → 10.0 Rolling-Upgrade Runbook

> Scope: a **generic, client-anonymized** runbook for rolling a single IBM MQ
> queue manager from **9.4.5.0** to the **10.0.0.0 LTS** base while it runs as a
> **Native HA Cross-Region Replication (CRR)** deployment — the "3+3" shape: a
> three-instance Native HA **Live** group in one region, asynchronously
> replicated to a matching three-instance **Recovery** group in another region.
> No client identifiers, host names, addresses, or secrets appear here;
> substitute your own. Placeholders are written `<LIKE_THIS>`.
>
> This runbook is the operational spine — **ordering, quiesce-and-assert gates,
> back-out, and DR validation.** For the CRR build itself see the companion
> [Native HA + CRR manual setup guide](nativeha-crr-setup-guide.md) and its
> [CRR TLS reference](nativeha-crr-tls-guide.md).
>
> **Grounding.** Every version-ordering rule, `dspmq` gate, point-of-no-return,
> and reversibility statement below is pinned to the Task 0 findings spike,
> [`docs/reports/2026-09-14-mq10-nativeha-crr-upgrade-facts-spike.md`](../reports/2026-09-14-mq10-nativeha-crr-upgrade-facts-spike.md),
> which in turn cites IBM Docs pages for IBM MQ 10.0 (product `SSYHRD_10.0.0`).
> Nothing here is asserted from memory. The queue-manager quiesce checks in §3
> are standard IBM MQ operational MQSC (stable product commands), called out as
> such where they are not themselves a 10.0-specific fact.
>
> Sources (IBM Docs, IBM MQ 10.0.x):
>
> - Upgrading a Native HA CRR configuration —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=crr-upgrading-native-ha-configurations>
> - `dspmq` reference —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=reference-dspmq-display-queue-managers>
> - Single-stage (in-place) migration —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=linux-migrating-aix-single-stage>
> - Planned switchover (CRR) —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=failover-complete-planned-switchover-native-ha-crr-configuration>
> - Unplanned failover (CRR) —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=failover-complete-unplanned-native-ha-crr-configuration>
> - Resolving a partitioned (split-brain) CRR —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=crr-resolving-partitioned-split-brain-problem>
> - Ubuntu apt upgrade —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=linux-upgrading-mq-installation-ubuntu-using-apt>
> - RHEL dnf upgrade —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=umil-upgrading-mq-installation-linux-red-hat-using-dnf>

## 1. Terminology and the shape being upgraded

A Native HA **group** is three log-replicating instances of one queue manager;
exactly one is *active* at a time and the other two are in-sync replicas. A
**CRR** (Cross-Region Replication) configuration pairs a **Live** group with a
**Recovery** group in *different regions*, linked by **asynchronous**
replication for disaster recovery. Both groups share the same queue-manager
name and configuration — six instances of one queue manager. CRR is distinct
from IRR (In-Region Replication) and from RDQM; this runbook covers the **CRR**
arm only.

Across the whole procedure, the version invariant in §2.1 is the single rule
that everything else serves: **the Recovery group must never be at a lower IBM
MQ version than the Live group.**

## 2. Ordering rules (read this first)

### 2.1 Across sites — Recovery group FIRST

> "…all instances in the group performing the recovery role must be at an IBM MQ
> version **equal to or higher than** the group performing the live role. **You
> cannot fail over or switch over to a group that is a lower version.** Because
> of this, you should apply upgrades to all members of the **recovery group
> before** applying it to all members of the live group… The upgrade of IBM MQ
> is only complete when all instances of both the recovery and live groups have
> been upgraded."
> — Upgrading a Native HA CRR configuration (IBM Docs, 10.0.x)

Consequences that drive the whole runbook:

- **Upgrade the Recovery group first, the Live group last.** At every moment the
  Recovery group's version must be **≥** the Live group's version.
- The upgrade is **not complete** — and the deployment is not back to full DR
  posture — until *both* groups are on 10.0.
- A fixpack-only move (the `F` in `V.R.M.F`) does not force this ordering, but
  a 9.4.5 → 10.0 move (a version/release change) does.

### 2.2 Within a group — replicas together, active last; never node-by-node

> "…you should aim to upgrade all your instances in a group as closely together
> as possible. **Do not adopt the cautious approach of updating one node,
> testing, and then updating the next node.** If you proceed in this way, you
> will limit the availability of your queue manager."
> — Upgrading a Native HA CRR configuration (IBM Docs, 10.0.x)

The three instances of a group are to be treated "as together constituting a
single queue manager." So the per-group unit of work is **the whole group at
once**, not a rolling node-by-node walk:

1. `endmqm` the group (which stops the active instance and its two replicas).
2. Upgrade the MQ installation on **all three** nodes — the two replicas and the
   former active alike (there is no in-service "active" during the group's
   window; the point of the Recovery-first ordering is that DR service is
   carried by the *other* group).
3. `strmqm` to bring the whole group back on 10.0.

Do **not** upgrade one node, test, then the next: that is the cautious
node-by-node approach the doc explicitly warns against, and it needlessly
extends the group's degraded window.

> Note (CRR vs IRR): the IRR sibling procedure adds a `SyncConsistency=Strict`
> caveat (a Strict Live instance stops if it cannot replicate to Recovery). The
> **CRR** path — this runbook — does not require that `SyncConsistency` dance for
> the switchover path.

### 2.3 The two supported end-to-end sequences

Both obey the same invariant (Recovery version ≥ Live version at all times).
Choose by how much downtime you can take.

**Sequence A — outage upgrade (simplest; queue manager unavailable for the whole
window).**

1. `endmqm` the **Recovery** group → upgrade all three Recovery nodes.
2. `endmqm` the **Live** group → upgrade all three Live nodes.
3. `strmqm` the Recovery group, then `strmqm` the Live group.

**Sequence B — low-downtime upgrade (uses a planned switchover; unavailable only
during the switchovers).**

1. `endmqm` the **Recovery** group → upgrade all three Recovery nodes → `strmqm`
   the Recovery group (now on 10.0, i.e. the *higher* version).
2. **Planned switchover** of the Live role *to* the now-upgraded (higher-version)
   group. See §3 gates and §5.
3. `endmqm` the original Live group (now the Recovery role) → upgrade its three
   nodes → `strmqm`.
4. **Switch back** if you want the original site Live again — legal now because
   both groups are on 10.0.

Sequence B is the recommended path where availability matters; §4.2 explains
exactly how far it is reversible.

## 3. Quiesce-and-assert — the pre-flight gate before every `endmqm`

Before ending any group (or requesting any switchover), assert the group is
**quiet and healthy**. Two layers of gate, both of which must pass.

### 3.1 Queue-manager quiesce (standard MQ operational MQSC)

Drain the queue manager so no in-flight work is lost when the group stops. These
are stable IBM MQ product commands (run `runmqsc <QMGR>` against the active
instance); they are the concrete form of "listener down, channels drained,
transmit-queue depths zero, no active application connections."

1. **Listener down** — stop inbound connections:

   ```mqsc
   STOP LISTENER(<LISTENER_NAME>)
   DISPLAY LSSTATUS(*) STATUS
   ```

   Assert: no listener reports `STATUS(RUNNING)`.

2. **Channels drained** — no message channel still moving work:

   ```mqsc
   DISPLAY CHSTATUS(*) STATUS INDOUBT
   ```

   Assert: every channel is `STATUS(INACTIVE)` or `STATUS(STOPPED)`, and none
   reports `INDOUBT(YES)`.

3. **Transmit-queue depths zero** — nothing queued for onward delivery:

   ```mqsc
   DISPLAY QSTATUS(<XMITQ>) CURDEPTH
   DISPLAY QUEUE(*) WHERE(USAGE EQ XMITQ) CURDEPTH
   ```

   Assert: `CURDEPTH(0)` on every transmission queue.

4. **No active application connections** — only MQ's own processes remain:

   ```mqsc
   DISPLAY CONN(*) TYPE(CONN) WHERE(APPLTYPE NE SYSTEM) CONNAME CHANNEL
   ```

   Assert: no user application connections listed.

Then end the group with a controlled (quiesce) shutdown:

```bash
endmqm -w <QMGR>     # controlled/quiesce end of this Native HA group
```

**Stop the mqweb server too — before the package upgrade.** Ending the queue
manager is **not** sufficient: the embedded web (mqweb) server also holds
`/opt/mqm` open, and the IBM MQ 10.0 package upgrade refuses to run while it is
up. Stop it before the `dnf`/`apt` step in §2.2:

```bash
endmqweb                     # or, in this lab: systemctl stop mqweb
```

Why this is not optional: with the mqweb server still running, the 10.0 RPM
`%prein` scriptlet **aborts** the upgrade with *"Installation of this fix pack
can not proceed because /opt/mqm is running. Run the command
/opt/mqm/bin/endmqweb"* — so the mqweb server must be down on every node of a
group before that group's MQ installation is upgraded. (Verified in the #1074
execution.)

### 3.2 Native HA / CRR group gate (`dspmq`, findings-pinned)

The group-level health gate is `dspmq -o nativeha -g`. Field semantics are from
the `dspmq` reference (IBM Docs, 10.0.x) as pinned in the findings note.

```bash
dspmq -o nativeha -g
```

Assert, **before each `endmqm` and before any switchover**:

- **`GRSTATUS(Normal)`** — the group is healthy (not `Synchronizing`,
  `Rebasing`, `Disconnected`, `Sync failed`, and above all **not
  `Partitioned`**, which is split-brain).
- **`CONNGRP(yes)`** on the remote group — the two groups can see each other.
  `CONNGRP(suspended)` flags a config incompatibility such as a version
  mismatch, and blocks a switchover.
- Before a **planned switchover** specifically, on the target (Recovery) group:
  **`INSYNC(yes)`** with **`BACKLOG(0)`** — the target can become Live with **no
  data loss** — and **`GRPVER`** confirming the target is at a version **≥** the
  current Live group (the §2.1 invariant, checked directly).

`RCOVLSN` / `RCOVTIME` report the log point the group could recover to; they
matter for the unplanned path (§4.2) and can be compared with `dmpmqlog` for
forensics. Do not proceed past any gate that is not green.

## 4. Back-out plan and the points of no return

There are **two distinct** point-of-no-return questions. Keep them separate.

### 4.1 The queue-manager migration point of no return (per group)

> "**Back up your system before you install a later version of IBM MQ over an
> earlier version. After you start a queue manager, you cannot revert to the
> previous version.** If you must restore the system, you cannot recover any
> work, such as changes to messages and objects, performed by the later version
> of IBM MQ."
> — Single-stage migration (IBM Docs, 10.0.x). The same page later:
> "At this point, queue manager data is migrated and you cannot revert to a
> previous release."

**The point of no return is the first `strmqm` under 10.0** against a given
group's queue-manager data. That start migrates the data in place; it can never
run under 9.4.5 again. There is **no in-place downgrade**.

**The only true back-out is restore-from-backup** — a backup of the
queue-manager data (and system) taken **while still on 9.4.5**, before that
first 10.0 `strmqm`. Any work performed under 10.0 after the backup is **forfeit**
on a restore.

Therefore:

- **Take a verified 9.4.5 backup of each group before that group's first 10.0
  `strmqm`.** This backup is the back-out. Do not treat "we can just reinstall
  9.4.5" as a back-out — reinstalling the binaries does not un-migrate the data.
- Before the first 10.0 `strmqm` anywhere, backing out is trivial: nothing has
  been migrated, so simply do not proceed (the 9.4.5 data is untouched).
- **Deferring the point of no return:** a **side-by-side** migration installs
  10.0 alongside 9.4.5 and keeps the queue manager associated with 9.4.5 until
  you explicitly stop it, remove the old version, and migrate — letting you
  install and verify 10.0 before committing. A single-stage (in-place) upgrade
  hits the point of no return the moment the group restarts under 10.0.

### 4.2 The CRR cutover point of no return — switchover vs failover

The CRR cutover has its own reversibility distinction, independent of §4.1.

**Planned switchover — reversible, RPO zero (the safe path).**

> "When you perform a planned switchover, the system takes steps to ensure that
> you do not lose data or encounter a partitioned (split-brain) problem… you can
> switch back to the original live group by repeating the process."
> — Planned switchover, CRR (IBM Docs, 10.0.x)

Requesting the switch puts both groups into a `Pending` role status while
recovery logs synchronise; only once synchronised do the groups adopt their new
roles. **No data loss, and it is reversible** — this is the mechanism Sequence B
(§2.3) relies on.

Interaction with the version invariant during an upgrade: once you switch the
Live role onto the **upgraded (10.0)** group, you **cannot switch back to a group
still on 9.4.5** — you "cannot switch over to a group that is a lower version"
(§2.1). So within Sequence B the switch-back in step 4 is legal **only after the
other group has also been upgraded to 10.0.** In other words, the practical
moment you can no longer abandon the upgrade and stay wholly on 9.4.5 is the
**first 10.0 `strmqm`** (§4.1) — the switchover itself remains a reversible role
move, but only between groups at compatible (≥) versions.

**Unplanned failover — the lossy, point-of-no-return path (avoid unless the Live
site is actually gone).**

> "…asynchronously. If the recovery group is not in-sync with the live group,
> data is lost when an unplanned failover occurs."
> — Unplanned failover, CRR (IBM Docs, 10.0.x)

Because replication is asynchronous, a Recovery group promoted after the Live
site is lost may be behind — **data written after its `RCOVLSN`/`RCOVTIME` is
lost.** Promotion sets `GroupRole=Live` **and** disables replication to the dead
site (`Enabled=False` on the recovery-group stanza) so the promoted group does
not try to replicate to it. If the old Live site later returns still in a Live
role you get two actives → **`GRSTATUS(Partitioned)`** (split-brain),
replication suspended; you must then choose which group to keep, compare logs
(`dspmq -o nativeha -g` `INITLSN`/`INITTIME` + `dmpmqlog`), set the discarded
group to `Recovery`, and delete its persistent data before rejoining. **Do not
use unplanned failover as part of a planned upgrade** — it is a disaster
response, not an upgrade step.

## 5. Post-upgrade DR validation — failover → failback

Once **both** groups are on 10.0 (the upgrade is "complete" per §2.1), prove the
DR posture with a **planned switchover** round trip. Use the planned
(reversible, zero-loss) path — not an unplanned failover — for routine
validation.

1. **Pre-checks (§3.2 gate):** `dspmq -o nativeha -g` shows both groups
   `GRSTATUS(Normal)`, the remote `CONNGRP(yes)`, and the current Recovery group
   `INSYNC(yes) BACKLOG(0)` with `GRPVER` ≥ the Live group. Both on 10.0.
2. **Switch over (Live → DR site):** request the planned switchover. Watch the
   groups pass through `Pending live` / `Pending recovery` and settle. Assert
   afterward: roles swapped (`GRPROLE` now `Live` on the DR group, `Recovery` on
   the origin), `GRSTATUS(Normal)`, **not** `Partitioned`, `CONNGRP(yes)`.
3. **Application reconnect:** confirm applications reconnect to the now-active
   instance (Native HA has no floating VIP — clients resolve the active
   instance) and that message flow resumes with no loss.
4. **Fail back (DR site → origin):** repeat the planned switchover in the other
   direction ("switch back … by repeating the process"). Re-assert the same
   gates. The deployment is back to its original Live/Recovery role assignment,
   fully on 10.0.

Record the `dspmq -o nativeha -g` output at each checkpoint as the validation
evidence.

> Lab-arm note: on the native-HA arm the switchover round trip is driven **today**
> by the Ansible playbook `ansible/site-nativeha-switchover.yml`, run directly:
>
> ```bash
> ansible-playbook site-nativeha-switchover.yml -e target_live=b   # cutover:  site A -> site B
> ansible-playbook site-nativeha-switchover.yml -e target_live=a   # failback: site B -> site A
> ```
>
> `target_live=b` makes site B the Live group (cutover); `target_live=a` restores
> site A as Live (failback). The playbook performs the `qm.ini` role edit
> directly — `GroupRole` in the `NativeHALocalInstance` stanza and `Enabled` in
> the `NativeHARecoveryGroup` stanza — then restarts the `mqmonitor@<QMGR>`
> systemd unit to apply the role swap; verify with `dspmq -o nativeha -g`. The
> switchover/failover IBM Docs illustrate role changes with container
> `oc`/`kubectl` commands, but on the VM arm the underlying mechanism is this
> direct `qm.ini` edit.
>
> The `mqlab dr cutover` / `mqlab dr failback` verbs do **not** drive the
> native-HA arm today: those verbs are **rdqm-only** (`src/mqlab/cli.py` refuses
> a non-rdqm stack — *"only the rdqm mechanism has an rdqm-dr-cutover flow"*).
> Native-HA `mqlab dr` wrappers over the playbook above are future/aspirational,
> tracked by the DR-commands automation epic
> (`vergil-project/.github#38`); until they land, use the playbook directly.

## 6. Ubuntu-arm applicability — same procedure

**The procedure above is identical on Ubuntu (including `arm64`) and on RHEL.**
The ordering rules (§2), the quiesce-and-assert gates (§3), the points of no
return and back-out (§4), and the DR validation (§5) are all OS- and
architecture-independent — they are properties of Native HA CRR and of the
queue-manager migration, not of the packaging.

The **only** thing that differs is the mechanical "upgrade the MQ installation
on this node" step inside §2.2:

- **Ubuntu (x86-64 and arm64):** upgrade the packages with `apt` per the Ubuntu
  apt-upgrade doc. The 10.0.0.0 developer media exists for both
  `UbuntuLinuxX64` and `UbuntuLinuxARM64` (findings §1.1), so an arm64 Ubuntu
  group upgrades by exactly the same steps as x86-64 — same ordering, same
  gates, same back-out.
- **RHEL (x86-64):** upgrade the packages with `dnf` per the RHEL dnf-upgrade
  doc.

Nothing in the CRR ordering or the point-of-no-return depends on which of these
package steps you run.
