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
> The concrete commands were executed against the lab on 2026-09-14 (the #1074
> execution). Nothing here is asserted from memory. The queue-manager quiesce
> checks in §3 are standard IBM MQ operational MQSC (stable product commands),
> called out as such where they are not themselves a 10.0-specific fact.
>
> Sources (IBM Docs, IBM MQ 10.0.x):
>
> - Upgrading a Native HA CRR configuration —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=crr-upgrading-native-ha-configurations>
> - `dspmq` reference —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=reference-dspmq-display-queue-managers>
> - Single-stage (in-place) migration —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=linux-migrating-aix-single-stage>
> - Upgrading an IBM MQ installation on Linux (parent) —
>   <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=mq-upgrading-installation-linux>
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

## 0. At a glance — the recommended (Sequence A) upgrade

This is the whole procedure as an **action-only checklist**, following the
recommended **Sequence A** (a planned maintenance-window outage — see §2.3 for
why this is the default, and §2.3 *Sequence B* for the low-downtime exception).
Upgrade the **Recovery** group first and the **Live** group second (§2.1), and
take each group all the way through steps 1–5 before starting the next. No
rationale here — every step links to its detailed section.

Placeholders used below: `<QM>` = queue-manager name; `<NODE>` = one node of a
group; `<XMITQ>` = a transmission queue; `<MQServer-dir>` = the directory of the
extracted 10.0 install media. Run per-node commands on **each of the three
nodes** of the group you are working on.

**For the Recovery group first — then repeat steps 1–5 for the Live group:**

1. **Confirm the group is healthy, then drain it** (§3.2, then §3.1). Check the
   group is `GRSTATUS(Normal)` / `CONNGRP(yes)`; then stop the listener and
   confirm channels, transmit queues, and application connections are all quiet.
2. **Stop all three instances and the web server** (§4.1). Stop each node's
   `mqmonitor@<QM>` systemd unit, then `mqweb`. Verify the group reports
   `Ended normally` / `QUORUM(0/3)` and no MQ process is left running.
3. **Back up the 9.4.5 data** (§4.2) — this backup is the *only* real back-out
   (§4.3). Take it now, while stopped, before any 10.0 start.
4. **Upgrade the packages to 10.0 on all three nodes** (§5.1 for entitled
   product media; §5.2 for the lab's developer media). Accept the licence and
   `dnf upgrade MQSeries*`; confirm `dspmqver` reports `10.0.0.0`.
5. **Start the group under 10.0 and verify** (§5.3). This first 10.0 start is the
   point of no return (§4.3) — the data migrates here. Start `mqmonitor@<QM>` on
   all three nodes, then `mqweb`; confirm `QUORUM(3/3)` / `HASTATUS(Normal)` and
   the group view `GRSTATUS(Normal)` / `CONNGRP(yes)` / `GRPVER(10.0.0.0)`.

**Once BOTH groups are on 10.0:**

6. **Validate DR** (§6): planned switchover to the DR site and back, asserting the
   group view each way.
7. **Functional sanity** (§6.1): put and get a test message on the active queue
   manager, confirm the round trip, then delete the test queue.

The upgrade is "complete" only when both groups are on 10.0 **and** DR is
validated (§2.1).

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

### 2.2 Within a group — all three instances together, never node-by-node

> "…you should aim to upgrade all your instances in a group as closely together
> as possible. **Do not adopt the cautious approach of updating one node,
> testing, and then updating the next node.** If you proceed in this way, you
> will limit the availability of your queue manager."
> — Upgrading a Native HA CRR configuration (IBM Docs, 10.0.x)

The three instances of a group are to be treated "as together constituting a
single queue manager." So the per-group unit of work is **the whole group at
once**, not a rolling node-by-node walk:

1. Stop **all three** instances of the group (§4.1). During the group's window
   there is no in-service "active" instance — the point of the Recovery-first
   ordering is that DR service is carried by the *other* group.
2. Upgrade the MQ installation on **all three** nodes — the two replicas and the
   former active alike (§5).
3. Start the whole group back up on 10.0 (§5.3).

Do **not** upgrade one node, test, then the next: that is the cautious
node-by-node approach the doc explicitly warns against, and it needlessly
extends the group's degraded window.

> Note (CRR vs IRR): IBM also offers **IRR** (In-Region Replication), a
> strict-sync alternative (`SyncConsistency=Strict`) where a Strict Live instance
> stops if it cannot replicate to Recovery. The lab **evaluated IRR and did not
> adopt it**: IRR is a two-node, single-instance-per-group topology (1 Live +
> 1 Recovery, **no in-group HA**) — DR *without* HA, not a mirror of this
> six-instance CRR arm — so the once-planned CRR-vs-IRR comparison was cut. See
> [`docs/reports/2026-09-15-mq10-nativeha-irr-setup-facts-spike.md`](../reports/2026-09-15-mq10-nativeha-irr-setup-facts-spike.md).
> The **CRR** path — this runbook — does not require that `SyncConsistency`
> handling on the switchover path.

### 2.3 Choose the sequence — Sequence A (outage) is the default

Both supported sequences obey the same invariant (Recovery version ≥ Live
version at all times). They differ only in how much downtime they take.

**Use Sequence A (planned maintenance-window outage) unless you have a hard
availability constraint.** Section 0 walks Sequence A end to end. The reasoning:

- This is a **major version** upgrade (9.4.5 → 10.0), not a fixpack or a minor
  update. A major change is not an "expected stable delta"; it must be treated
  specially and belongs in a **planned maintenance window with the application
  quiesced**.
- The target infrastructure has **no zero-downtime requirement**, so the extra
  risk of upgrading under live traffic buys nothing.
- The outage path lets you **validate the upgraded deployment and assert it is
  correct *before* go-live**. A live upgrade does the opposite: the moment you
  return to service you are already carrying real traffic on the new version, so
  any regression is exposed under load *before* you have had a chance to check
  for it. Keeping a queue manager live through an upgrade is reasonable for a
  patch or minor upgrade (a stable, expected delta); a major version change is
  not the place for it.

**Sequence A — outage upgrade (recommended).** The queue manager is unavailable
during its group's window. Per group, Recovery first:

1. Quiesce and assert the group is healthy and quiet (§3).
2. Stop all three instances and the mqweb server (§4.1).
3. Back up the 9.4.5 data (§4.2).
4. Upgrade the packages to 10.0 on all three nodes (§5).
5. Start the group under 10.0 and verify (§5.3).

Then repeat 1–5 for the Live group. Because the Recovery group is fully back on
10.0 before you touch the Live group, the two groups are never down at the same
time, and the §2.1 invariant holds throughout (Recovery reaches the higher
version first).

**Sequence B — low-downtime upgrade (the exception; only for a genuine
availability constraint).** Uses a planned switchover, so the queue manager is
unavailable only during the brief switchovers:

1. Quiesce (§3) → stop (§4.1) → back up (§4.2) → upgrade (§5) → start (§5.3) the
   **Recovery** group, so it returns on 10.0 (the higher version).
2. **Planned switchover** of the Live role onto the now-upgraded group (§6).
3. Quiesce → stop → back up → upgrade → start the **original Live** group (which
   now holds the Recovery role).
4. **Switch back** if you want the original site Live again — legal only now that
   both groups are on 10.0 (§4.4).

Sequence B trades validate-before-go-live for availability; §4.4 explains exactly
how far it is reversible. **Prefer Sequence A.**

## 3. Quiesce-and-assert — the pre-flight gate before you stop a group

Before stopping any group (or requesting any switchover), assert the group is
**quiet and healthy**. Two layers of gate, both of which must pass. Do not
proceed past any gate that is not green.

### 3.1 Queue-manager quiesce (standard MQ operational MQSC)

Drain the queue manager so no in-flight work is lost when the group stops. These
are stable IBM MQ product commands. Open an MQSC session against the **active**
instance:

```bash
runmqsc <QM>
```

Then run the checks below (each is an MQSC command typed at the `runmqsc`
prompt). They are the concrete form of "listener down, channels drained,
transmit-queue depths zero, no active application connections."

1. **Listener down** — stop new inbound connections, then confirm none is
   running:

   ```mqsc
   STOP LISTENER(<LISTENER_NAME>)
   DISPLAY LSSTATUS(*) STATUS
   ```

   Assert: no listener reports `STATUS(RUNNING)`.

2. **Channels drained** — no message channel is still moving work, and none is
   stuck mid-transaction:

   ```mqsc
   DISPLAY CHSTATUS(*) STATUS INDOUBT
   ```

   Assert: every channel is `STATUS(INACTIVE)` or `STATUS(STOPPED)`, and none
   reports `INDOUBT(YES)`.

3. **Transmit-queue depths zero** — nothing is queued for onward delivery to
   another queue manager:

   ```mqsc
   DISPLAY QSTATUS(<XMITQ>) CURDEPTH
   DISPLAY QUEUE(*) WHERE(USAGE EQ XMITQ) CURDEPTH
   ```

   Assert: `CURDEPTH(0)` on every transmission queue.

4. **No active application connections** — only MQ's own processes remain
   connected:

   ```mqsc
   DISPLAY CONN(*) TYPE(CONN) WHERE(APPLTYPE NE SYSTEM)
   ```

   Assert: no user-application connections are listed.

Type `END` to leave the MQSC session. The actual stop of the instances happens
in §4.1 (this deployment stops them via systemd, **not** `endmqm`).

### 3.2 Native HA / CRR group gate (`dspmq`, findings-pinned)

The group-level health gate is `dspmq -o nativeha -g`. Field semantics are from
the `dspmq` reference (IBM Docs, 10.0.x) as pinned in the findings note.

```bash
dspmq -m <QM> -o nativeha -g
```

A healthy CRR group line looks like this (real, from the executed run — Recovery
group shown):

```text
QMNAME(<QM>) ... GRPNAME(Recovery) GRPROLE(Recovery) GRPVER(10.0.0.0) CONNGRP(yes) GRSTATUS(Normal)
```

Assert, **before you stop each group and before any switchover**:

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
matter for the unplanned path (§4.4) and can be compared with `dmpmqlog` for
forensics.

## 4. Stop the group, back it up, and the points of no return

### 4.1 Stop all three instances (systemd) and the mqweb server

**In this deployment the queue-manager instances run under systemd**, supervised
by a per-queue-manager `mqmonitor@<QM>` unit. You therefore stop an instance by
stopping its unit — **not** with a bare `endmqm`, which the monitor would simply
restart. Stop the unit on **each of the three nodes** of the group:

```bash
# Run on EACH of the three nodes of the group:
sudo systemctl stop mqmonitor@<QM>
```

Then stop the **mqweb** (embedded web) server on each node. This is **required,
not optional**: the mqweb server holds `/opt/mqm` open, and the IBM MQ 10.0
package upgrade **refuses to run while it is up** — the RPM `%prein` scriptlet
aborts with *"Installation of this fix pack can not proceed because /opt/mqm is
running. Run the command /opt/mqm/bin/endmqweb"*.

```bash
# Run on EACH node (either form works):
sudo systemctl stop mqweb
# or, as the mqm user:
endmqweb
```

Verify the group is fully stopped and no MQ process remains:

```bash
dspmq -m <QM> -o nativeha
# → ... STATUS(Ended normally)

dspmq -m <QM> -o nativeha -x
# → ... QUORUM(0/3)          (no instances participating)

ps -ef | grep /opt/mqm | grep -v grep
# → (no output — no IBM MQ process still running)
```

Do not proceed to the backup until every node of the group is stopped and clean.

### 4.2 Back up the 9.4.5 data — the only true back-out

Take the backup **now, while the group is stopped, and before its first 10.0
start.** This backup is the back-out (§4.3); once you start under 10.0 the data
is migrated in place and cannot be un-migrated. Back up the queue-manager data
and logs on **each of the three nodes** (each instance holds its own copy):

```bash
# Run on EACH of the three nodes of the group:
sudo tar czf /root/<qm>-9.4.5.0-backup.tar.gz -C /var/mqm qmgrs/<QM> log/<QM>
```

`<qm>` is the (lower-cased) queue-manager name; `/var/mqm/qmgrs/<QM>` and
`/var/mqm/log/<QM>` are the queue manager's object/message data and its recovery
logs. Keep the archive off the node (copy it somewhere safe). IBM's generic
guidance is the same: "Back up your data" before upgrading (RHEL dnf-upgrade doc,
step *"Backed up your data"*; single-stage migration doc — see Sources).

### 4.3 The queue-manager migration point of no return (per group)

> "**Back up your system before you install a later version of IBM MQ over an
> earlier version. After you start a queue manager, you cannot revert to the
> previous version.** If you must restore the system, you cannot recover any
> work, such as changes to messages and objects, performed by the later version
> of IBM MQ."
> — Single-stage migration (IBM Docs, 10.0.x). The same page later:
> "At this point, queue manager data is migrated and you cannot revert to a
> previous release."

**The point of no return is the first start under 10.0** (the first
`systemctl start mqmonitor@<QM>` on 10.0 binaries — §5.3) against a given group's
queue-manager data. That start migrates the data in place; it can never run
under 9.4.5 again. There is **no in-place downgrade**.

**The only true back-out is restore-from-backup** — the §4.2 backup taken while
still on 9.4.5, before that first 10.0 start. Any work performed under 10.0 after
the backup is **forfeit** on a restore.

Therefore:

- **Take a verified 9.4.5 backup of each group before that group's first 10.0
  start** (§4.2). Do not treat "we can just reinstall 9.4.5" as a back-out —
  reinstalling the binaries does not un-migrate the data.
- Before the first 10.0 start anywhere, backing out is trivial: nothing has been
  migrated, so simply do not proceed (the 9.4.5 data is untouched).
- **Deferring the point of no return:** a **side-by-side** migration installs
  10.0 alongside 9.4.5 and keeps the queue manager associated with 9.4.5 until
  you explicitly stop it, remove the old version, and migrate — letting you
  install and verify 10.0 before committing. The single-stage (in-place) upgrade
  in this runbook hits the point of no return the moment the group restarts under
  10.0.

### 4.4 The CRR cutover point of no return — switchover vs failover

The CRR cutover has its own reversibility distinction, independent of §4.3.

**Planned switchover — reversible, RPO zero (the safe path).**

> "When you perform a planned switchover, the system takes steps to ensure that
> you do not lose data or encounter a partitioned (split-brain) problem… you can
> switch back to the original live group by repeating the process."
> — Planned switchover, CRR (IBM Docs, 10.0.x)

Requesting the switch puts both groups into a `Pending` role status while
recovery logs synchronise; only once synchronised do the groups adopt their new
roles. **No data loss, and it is reversible** — this is the mechanism Sequence B
(§2.3) and the DR validation (§6) rely on.

Interaction with the version invariant during an upgrade: once you switch the
Live role onto the **upgraded (10.0)** group, you **cannot switch back to a group
still on 9.4.5** — you "cannot switch over to a group that is a lower version"
(§2.1). So within Sequence B the switch-back in step 4 is legal **only after the
other group has also been upgraded to 10.0.** The practical moment you can no
longer abandon the upgrade and stay wholly on 9.4.5 is the **first 10.0 start**
(§4.3) — the switchover itself remains a reversible role move, but only between
groups at compatible (≥) versions.

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

## 5. Upgrade the MQ packages to 10.0, then start under 10.0

Do this on **all three nodes of the group together** (§2.2), after the group is
stopped (§4.1) and backed up (§4.2). The package mechanics — the `dnf`
transaction and the `MQSeries*` package names — are **identical** for entitled
production IBM MQ and for the free MQ Advanced for Developers media. The only
differences are (1) where the media comes from and (2) which licence
`mqlicense.sh` makes you accept. §5.1 is the primary (production) path; §5.2 is
the lab's developer-media variant.

### 5.1 Product path (primary) — entitled IBM MQ on RHEL with dnf

Grounded in the RHEL dnf-upgrade doc (IBM Docs, 10.0.x — see Sources;
`build/refs/ibm-docs/ibm-mq/10.0.x/umil-upgrading-mq-installation-linux-red-hat-using-dnf/content.txt`).
The doc's pre-upgrade steps — stop applications, `endmqweb`, stop listeners, stop
the queue managers, and back up — are exactly what §3 and §4 accomplish (with the
Native HA adaptation that instances are stopped via systemd, §4.1).

The version you upgrade **from** must be IBM MQ 9.2.0 or later; from 9.4.0 or
higher a fixpack may be installed (the `F` in `V.R.M.F` need not be 0), so
9.4.5.0 → 10.0.0.0 is supported (dnf-upgrade doc, *Before you begin*).

1. **Stage your entitled 10.0 production media.** Obtain the 10.0 production
   installation image from your IBM entitlement (Passport Advantage / Fix
   Central). If it is a downloadable image, decompress and extract it with GNU
   tar (`gtar`):

   ```bash
   gunzip <part>.tar.gz
   tar -xvf <part>.tar        # GNU tar (gtar) is required
   ```

   > `<part>` is the file name of your entitled installation image. **Do not
   > assume a file name or part number here** — the production media identifiers
   > are environment- and entitlement-specific; use whatever your Passport
   > Advantage / Fix Central download is actually called.

2. **Go to the package directory** (where the extracted `MQSeries*.rpm` files
   and `mqlicense.sh` live):

   ```bash
   cd <MQServer-dir>
   ```

   > Optional (only if this is not the sole MQ installation on the host, or it is
   > in a non-default path): run `./crtmqpkg <suffix> <installationPath>` first to
   > create a uniquely-named package set, then `dnf ... upgrade 'MQSeries*<suffix>*'`
   > below (dnf-upgrade doc).

3. **Accept the production licence:**

   ```bash
   ./mqlicense.sh -text_only     # accept the PRODUCTION licence terms
   ```

   > The cached dnf-upgrade doc documents `./mqlicense.sh` (X-window) and
   > `./mqlicense.sh -text_only` (screen-reader/plain text). The executed lab run
   > used the non-interactive `./mqlicense.sh -accept` flag (§5.2); that flag is
   > **not** shown on the cached page, so confirm the exact acceptance flag
   > against your media's `mqlicense.sh` help before relying on it in production.

4. **Point dnf at the media and upgrade all installed MQ components.** Per the
   doc, add a repository file under `/etc/yum.repos.d/` (e.g. `IBM_MQ.repo`) whose
   `baseurl=file:///<installationFilesLocation>` points at the extracted media,
   then:

   ```bash
   sudo dnf clean all
   sudo dnf repolist              # confirm the IBM MQ repo is listed
   sudo dnf -y upgrade 'MQSeries*'
   ```

5. **Verify the installed version is 10.0:**

   ```bash
   dspmqver
   # Version banner should read 10.0.0.0

   dspmqver -b -f 2
   # → 10.0.0.0
   ```

> Setting the primary installation: the executed lab run also ran
> `sudo /opt/mqm/bin/setmqinst -i -p /opt/mqm` after the upgrade to mark
> `/opt/mqm` as the primary installation (§5.2). This step is **not** part of the
> cached dnf-upgrade doc's procedure; it applies when you keep a single primary
> installation at `/opt/mqm`. Run it only if your installation model uses a
> primary installation; skip it for multi-install / non-default layouts.

### 5.2 Developer-media variant (the lab)

The lab upgrades from the **free MQ Advanced for Developers** media, not entitled
production media. The `dnf` mechanics and the `MQSeries*` package names are the
same as §5.1; the two differences are:

1. **Media source** — the free developer tarball from
   `public.dhe.ibm.com` (MQ Advanced for Developers), instead of the entitled
   10.0 production media from Passport Advantage / Fix Central.
2. **Licence** — `mqlicense.sh` makes you accept the **developer** licence, not
   the production licence.

The exact commands executed against the lab on 2026-09-14 (all three nodes of the
group), which upgrade directly against the RPM files in the extracted media
directory rather than via a repository file:

```bash
cd <MQServer-dir> \
  && ./mqlicense.sh -accept \
  && sudo dnf upgrade -y MQSeries*.rpm \
  && sudo /opt/mqm/bin/setmqinst -i -p /opt/mqm

dspmqver -b -f 2
# → 10.0.0.0
```

### 5.3 Start the group under 10.0 and verify (the point of no return)

Once **all three** nodes of the group show `dspmqver` = `10.0.0.0`, start the
group. **This first 10.0 start migrates the queue-manager data in place — the
point of no return (§4.3).** Start each node's instance, then its mqweb server:

```bash
# Run on EACH of the three nodes of the group:
sudo systemctl start mqmonitor@<QM>
sudo systemctl start mqweb
```

Verify the group came back healthy on 10.0:

```bash
dspmq -m <QM> -o nativeha -x
# healthy example: ... QUORUM(3/3) ... ROLE(Active) ... HASTATUS(Normal)

dspmq -m <QM> -o nativeha -g
# healthy example: ... GRSTATUS(Normal) ... CONNGRP(yes) ... GRPVER(10.0.0.0)
```

Assert `QUORUM(3/3)` and `HASTATUS(Normal)` (all three instances back and one
active), `GRSTATUS(Normal)` (not `Partitioned`), `CONNGRP(yes)` (the peer group
is reachable), and `GRPVER(10.0.0.0)`. Only then move on:

- If this was the **Recovery** group, go back to §3 and run steps 1–5 for the
  **Live** group.
- If this was the **Live** group (i.e. both groups are now on 10.0), go to §6.

### 5.4 Ubuntu and arm64 — same procedure

**The procedure above is identical on Ubuntu (including `arm64`) and on RHEL.**
The ordering rules (§2), the quiesce-and-assert gates (§3), the stop/back-up and
points of no return (§4), and the DR validation (§6) are all OS- and
architecture-independent — they are properties of Native HA CRR and of the
queue-manager migration, not of the packaging.

The **only** thing that differs is the mechanical package step in §5:

- **Ubuntu (x86-64 and arm64):** upgrade the packages with `apt` per the Ubuntu
  apt-upgrade doc (see Sources) instead of `dnf`. The 10.0.0.0 developer media
  exists for both `UbuntuLinuxX64` and `UbuntuLinuxARM64` (findings §1.1), so an
  arm64 Ubuntu group upgrades by exactly the same steps as x86-64 — same
  ordering, same gates, same back-out.
- **RHEL (x86-64):** upgrade the packages with `dnf` as in §5.1 / §5.2.

Nothing in the CRR ordering or the point-of-no-return depends on which package
step you run.

## 6. Post-upgrade DR validation — switchover → failback

Once **both** groups are on 10.0 (the upgrade is "complete" per §2.1), prove the
DR posture with a **planned switchover** round trip. Use the planned (reversible,
zero-loss) path — not an unplanned failover — for routine validation.

1. **Pre-checks (§3.2 gate):** confirm both groups are `GRSTATUS(Normal)`, the
   remote `CONNGRP(yes)`, and the current Recovery group `INSYNC(yes)
   BACKLOG(0)` with `GRPVER` ≥ the Live group. Both on 10.0.

   ```bash
   dspmq -m <QM> -o nativeha -g
   ```

2. **Switch over (Live → DR site):**

   ```bash
   ansible-playbook site-nativeha-switchover.yml -e target_live=b   # cutover: site A -> site B
   ```

   `target_live=b` makes site B the Live group. Watch the groups pass through
   `Pending live` / `Pending recovery` and settle, then re-check the group view:
   roles swapped (`GRPROLE(Live)` on the DR group, `GRPROLE(Recovery)` on the
   origin), `GRSTATUS(Normal)`, **not** `Partitioned`, `CONNGRP(yes)`, and
   `GRPVER(10.0.0.0)` on both.

3. **Application reconnect:** confirm applications reconnect to the now-active
   instance (Native HA has no floating VIP — clients resolve the active instance)
   and that message flow resumes with no loss.

4. **Fail back (DR site → origin):**

   ```bash
   ansible-playbook site-nativeha-switchover.yml -e target_live=a   # failback: site B -> site A
   ```

   `target_live=a` restores site A as Live. Re-assert the same gates. The
   deployment is back to its original Live/Recovery role assignment, fully on
   10.0.

Record the `dspmq -o nativeha -g` output at each checkpoint as the validation
evidence.

> Lab vs. non-lab mechanism. On this lab's native-HA arm the switchover round
> trip is driven **today** by the Ansible playbook
> `ansible/site-nativeha-switchover.yml` (run directly, as above). Under the hood
> the playbook performs the `qm.ini` role edit directly — `GroupRole` in the
> `NativeHALocalInstance` stanza and `Enabled` in the `NativeHARecoveryGroup`
> stanza — then restarts the `mqmonitor@<QM>` systemd unit across all instances of
> both groups to apply the role swap. **In a non-lab context** where you have no
> such playbook, that *is* the manual procedure: edit `qm.ini` (`GroupRole` /
> `Enabled`) on every instance of both groups, then
> `sudo systemctl restart mqmonitor@<QM>` on each, and verify with
> `dspmq -o nativeha -g`. The switchover/failover IBM Docs illustrate the same
> role change with container `oc`/`kubectl` commands; on the VM arm the underlying
> mechanism is this direct `qm.ini` edit.
>
> The `mqlab dr cutover` / `mqlab dr failback` verbs do **not** drive the
> native-HA arm today: those verbs are **rdqm-only** (`src/mqlab/cli.py` refuses
> a non-rdqm stack — *"only the rdqm mechanism has an rdqm-dr-cutover flow"*).
> Native-HA `mqlab dr` wrappers over the playbook above are future/aspirational,
> tracked by the DR-commands automation epic (`vergil-project/.github#38`); until
> they land, use the playbook directly.

### 6.1 Functional sanity check on the active 10.0 queue manager

Prove the upgraded queue manager actually passes and delivers messages. Define a
throwaway queue, round-trip one message through it with the shipped sample
programs, then delete it.

Create the test queue:

```bash
runmqsc <QM>
DEFINE QLOCAL(UPGRADE.TEST)
END
```

Put a message, then get it back (`amqsput` / `amqsget` are the IBM MQ sample
apps under `/opt/mqm/samp/bin`; `amqsput` sends each line you type and ends on a
blank line, `amqsget` reads what is on the queue and exits after a short wait):

```bash
echo 'hello 10.0' | amqsput UPGRADE.TEST <QM>
amqsget UPGRADE.TEST <QM>
# → the 'hello 10.0' message is read back, confirming the round trip
```

Delete the test queue:

```bash
runmqsc <QM>
DELETE QLOCAL(UPGRADE.TEST)
END
```

With DR validated (§6) and this round trip confirmed on the active 10.0 queue
manager, the 9.4.5 → 10.0 upgrade is complete.
