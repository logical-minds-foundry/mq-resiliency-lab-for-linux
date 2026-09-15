# IBM MQ Native HA CRR upgrade guide — 9.4.5.0 → 10.0.0.0 (RHEL)

A step-by-step, self-contained procedure for rolling an IBM MQ **Native HA
Cross-Region Replication (CRR)** deployment from **9.4.5.0** to **10.0.0.0**
during a maintenance window on **Red Hat Enterprise Linux**. It is written for a
competent MQ administrator who wants explicit do-this steps: every command is in
its own block with the output you should expect. Substitute your own values for
the placeholders (`<QM>` = queue-manager name, `<LISTENER>` = listener name);
this guide uses `recovery-1/2/3` for the three Recovery-group nodes and
`live-1/2/3` for the three Live-group nodes.

## Purpose and scope

This guide upgrades one queue manager deployed as a Native HA CRR "3+3" pair —
a three-instance **Live** group in one region asynchronously replicated to a
matching three-instance **Recovery** group in another region — from IBM MQ
9.4.5.0 to the 10.0.0.0 LTS base, using the **entitled production media** and
the RHEL `dnf` package manager. It assumes: you hold entitled IBM MQ 10.0
production media; the deployment is a **healthy** 3+3 CRR queue manager on
9.4.5.0; you have an agreed **maintenance window** (the queue manager is
unavailable to applications while its serving group is down); and the back-out
decision (see [Back-out plan](#back-out-plan)) has been made **before** you
start. Only this topology, OS, and version step are in scope, and only the
outage (maintenance-window) sequence — no low-downtime switchover variant.

---

## §0 — At a glance

The whole procedure on one screen. Do the **Recovery group first**, then the
**Live group**; do **not** interleave nodes. Each line links to its detailed
step.

**Recovery group (recovery-1/2/3):**

1. [Quiesce the Recovery group](#41-recovery-group--quiesce) — stop message flow at the edges.
2. [Back up each instance](#42-recovery-group--back-up-each-instance) — pre-upgrade due-diligence copy.
3. [Stop the instances (and mqweb)](#43-recovery-group--stop-the-instances-and-mqweb) — nothing under `/opt/mqm` left running.
4. [Upgrade the packages](#44-recovery-group--upgrade-the-packages) — `dnf -y upgrade 'MQSeries*'` from entitled 10.0 media.
5. [Start under 10.0 and verify](#45-recovery-group--start-under-100-and-verify-point-of-no-return) — **point of no return**; check quorum and group health.

6. [Confirm the mixed-version boundary](#46-confirm-the-mixed-version-boundary) — Recovery on 10.0, Live still on 9.4.5, CRR still connected.

**Live group (live-1/2/3) — repeat the same five steps:**

7. [Quiesce the Live group](#471-quiesce-the-live-group) — the application-facing outage begins here.
8. [Back up each instance](#472-back-up-each-instance).
9. [Stop the instances (and mqweb)](#473-stop-the-instances-and-mqweb).
10. [Upgrade the packages](#474-upgrade-the-packages).
11. [Start under 10.0 and verify](#475-start-under-100-and-verify).

**Then:**

12. [Validate: health, quorum, CRR](#validate-and-go-live) — both groups on 10.0 and connected.
13. [Prove DR: planned switchover round trip](#dr-proof--planned-switchover-round-trip) — switch over and switch back.
14. [Functional sanity: put/get](#functional-sanity--putget) — a persistent message survives the round trip.
15. [Go live](#go-live) — re-enable the listener, resume applications.

---

## Before you start

**The shape.** A Native HA *group* is three log-replicating instances of one
queue manager; one instance is elected active (in the Live group) or leader (in
the Recovery group), and the other two are in-sync replicas kept current by
Raft-replicated recovery logs. A **CRR** configuration pairs a **Live** group
with a **Recovery** group in a different region, linked by **asynchronous**
replication for disaster recovery — six instances of the same queue manager,
sharing one name and configuration. Applications connect to the **Live** group;
the **Recovery** group receives replication and stands by to take over.

**Two hard rules — do not break either:**

1. **Upgrade the Recovery group first, the Live group last.** IBM: *"you should
   apply upgrades to all members of the recovery group before applying it to all
   members of the live group. The upgrade of IBM MQ is only complete when all
   instances of both the recovery and live groups have been upgraded."*
   ([Upgrading Native HA CRR configurations][crr-upg])
2. **The Recovery group's version must always be ≥ the Live group's version.**
   IBM: *"all instances in the group performing the recovery role must be at an
   IBM MQ version equal to or higher than the group performing the live role.
   You cannot fail over or switch over to a group that is a lower version."*
   ([Upgrading Native HA CRR configurations][crr-upg]) Rule 1 is what keeps rule
   2 true throughout: bringing the Recovery group to 10.0 first means it is never
   behind the Live group.

**Treat each group as one unit — never node-by-node.** IBM: *"you should aim to
upgrade all your instances in a group as closely together as possible. Do not
adopt the cautious approach of updating one node, testing, and then updating the
next node. If you proceed in this way, you will limit the availability of your
queue manager."* ([Upgrading Native HA CRR configurations][crr-upg]) So the unit
of work is a whole group: stop all three instances, upgrade all three, start all
three.

**The point of no return** is the **first `strmqm` under 10.0** against a group.
That start migrates the queue-manager data in place, one-way — it can never run
under 9.4.5 again. Everything before it is reversible (the 9.4.5 data is
untouched); nothing after it is cleanly reversible. See
[Back-out plan](#back-out-plan).

**What "quiesce" means here.** Quiesce = stop message **flow at the edges**, not
empty the queues:

- stop the **listener** (no new inbound connections),
- stop the **channels** (no message channel still moving work),
- **stop or disconnect the applications**, and
- confirm **no active application connections** remain
  (`DISPLAY CONN(*) TYPE(CONN) WHERE(APPLTYPE NE SYSTEM)` returns nothing).

You do **not** drain queues to zero. **Persistent** messages left in the queues
are expected and ride through the migration. **Non-persistent** messages do
**not** survive the restart — that is true of any queue-manager restart, not
something specific to this upgrade — so state that plainly to application owners
before the window.

Throughout, wherever a step says to check logs, check the queue manager error
logs (`AMQERR*.LOG` under the queue manager's `errors` directory, or syslog, per
your diagnostic-logging configuration).

---

## The upgrade, step by step

### 4.1 Recovery group — quiesce

Because applications connect to the **Live** group, the Recovery group is not
serving application traffic; quiescing it is mostly a confirmation that nothing
is attached, followed by a clean stop. Run MQSC against the leader instance:

```mqsc
DISPLAY CONN(*) TYPE(CONN) WHERE(APPLTYPE NE SYSTEM)
```

Expected: no connections are listed (every remaining connection is a SYSTEM
connection, which the filter excludes). If any user application connection
appears, resolve it before continuing.

If a listener is defined on the Recovery group, stop it too:

```mqsc
STOP LISTENER(<LISTENER>)
```

```text
     1 : STOP LISTENER(<LISTENER>)
AMQ8005I: IBM MQ listener stopped.
```

### 4.2 Recovery group — back up each instance

Take a pre-upgrade copy on each instance **while the instance is stopped** (do
this as part of the stop step below, or stop, back up, and leave stopped). Back
up the data directory **and** the log directory **together** (all-or-nothing),
preserving ownership with `tar`, plus the configuration files:

```bash
# As root/mqm, with this instance's queue manager stopped:
tar -cvf /backup/<QM>-recovery-1-preupgrade.tar \
    /var/mqm/qmgrs/<QM> /var/mqm/log/<QM>
cp /var/mqm/qmgrs/<QM>/qm.ini /backup/<QM>-recovery-1-qm.ini
cp /var/mqm/mqs.ini          /backup/<QM>-recovery-1-mqs.ini
```

Read [Back-out plan](#back-out-plan) before relying on this: for a Native HA
group this per-instance copy is **not** a sanctioned Native HA backup and is not
a clean rollback path. Take it as due diligence, not as your recovery plan.

### 4.3 Recovery group — stop the instances (and mqweb)

Stop the queue-manager instance on **each** of the three Recovery nodes with a
controlled (wait) shutdown:

```bash
endmqm -w <QM>      # run on recovery-1, recovery-2, and recovery-3
```

```text
Quiesce request accepted. The queue manager will stop when all outstanding work is complete.
IBM MQ queue manager '<QM>' ending.
IBM MQ queue manager '<QM>' ended.
```

**Portability note — process monitors.** Native HA instances are commonly kept
running by a process monitor (the IBM-shipped `mqmonitor@` systemd sample, or
your own unit). If one is in use, a bare `endmqm` will just be undone — the
monitor restarts the instance. Stop the **unit** on each node instead:

```bash
systemctl stop mqmonitor@<QM>      # or your own unit name; run on each of the 3 nodes
```

**Also stop the MQ web / REST server on each node** — this is required, not
optional. The IBM MQ 10.0 RPM `%prein` scriptlet aborts the package upgrade if
anything under `/opt/mqm` is still running (`Installation of this fix pack can
not proceed because /opt/mqm is running. Run the command
/opt/mqm/bin/endmqweb`):

```bash
endmqweb              # or: systemctl stop mqweb
```

Verify nothing under `/opt/mqm` is left running on each node before you upgrade:

```bash
ps -ef | grep /opt/mqm | grep -v grep
```

Expected: **no output**. If anything is listed, stop it before proceeding.

### 4.4 Recovery group — upgrade the packages

Do this on **all three** Recovery nodes. Obtain your **entitled IBM MQ 10.0
production media** (referred to below as `<entitled 10.0 media>` — use the exact
filename/part number from your Passport Advantage entitlement; do not guess it),
then follow the IBM RHEL `dnf` upgrade procedure
([Upgrading an IBM MQ installation on Linux Red Hat using dnf][dnf-upg]).

Extract the media and change into the package directory:

```bash
gunzip <entitled 10.0 media>.tar.gz
tar -xvf <entitled 10.0 media>.tar     # use GNU tar (gtar)
cd <extracted-package-directory>
```

Accept the licence. The documented forms are an X-window prompt (`./mqlicense.sh`)
or an in-shell text prompt (`./mqlicense.sh -text_only`); if your media supports
a non-interactive accept flag, confirm its exact spelling against the media's
readme rather than assuming one:

```bash
./mqlicense.sh -text_only
```

```text
Licensed Materials - Property of IBM
...
Agreement accepted: Proceed with install.
```

Point `dnf` at the media (add the IBM MQ repository file under
`/etc/yum.repos.d/` per the IBM procedure, then `dnf clean all` and
`dnf repolist` to confirm it is visible), then upgrade all installed IBM MQ
components:

```bash
dnf -y upgrade 'MQSeries*'
```

```text
...
Upgraded:
  MQSeriesRuntime-10.0.0.0-...   MQSeriesServer-10.0.0.0-...   MQSeriesClient-10.0.0.0-...
  ...
Complete!
```

Verify the installed version on each node:

```bash
dspmqver -b -f 2
```

```text
10.0.0.0
```

### 4.5 Recovery group — start under 10.0 and verify (POINT OF NO RETURN)

> **This is the point of no return for the Recovery group.** The first start
> under 10.0 migrates the queue-manager data in place, one-way. There is no
> in-place downgrade afterward. See [Back-out plan](#back-out-plan).

Start the instance on **each** of the three Recovery nodes (or start the monitor
unit if you used one):

```bash
strmqm <QM>          # run on recovery-1, recovery-2, and recovery-3
                     # (or: systemctl start mqmonitor@<QM> on each node)
```

```text
IBM MQ queue manager '<QM>' starting.
The queue manager is associated with installation 'Installation1'.
Log replay for queue manager '<QM>' complete.
IBM MQ queue manager '<QM>' started using V10.0.0.0.
```

Verify the group is healthy at the instance level. In the **Recovery** group the
elected instance reports `ROLE(Leader)` (the recovery-role equivalent of
`ROLE(Active)`) — that is the healthy state for a group in the recovery role:

```bash
dspmq -m <QM> -o nativeha -x
```

```text
QMNAME(<QM>)                                    STATUS(Running)
  INSTANCE(recovery-1) ROLE(Leader)  INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
  INSTANCE(recovery-2) ROLE(Replica) INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
  INSTANCE(recovery-3) ROLE(Replica) INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
```

Assert: `QUORUM(3/3)`, exactly one instance `ROLE(Leader)` and two
`ROLE(Replica)`, every instance `INSYNC(yes)` and `HASTATUS(Normal)`. If quorum
is not 3/3 or an instance is not in sync, check the error logs before
continuing.

Then verify group and CRR health (field semantics from the
[`dspmq` reference][dspmq]):

```bash
dspmq -m <QM> -o nativeha -g
```

```text
QMNAME(<QM>)  GRPNAME(recovery)  GRPROLE(Recovery)  GRSTATUS(Normal)  GRPVER(10.0.0.0)
QMNAME(<QM>)  GRPNAME(live)      GRPROLE(Live)      GRSTATUS(Normal)  GRPVER(9.4.5.0)   CONNGRP(yes)
```

Assert: local group `GRSTATUS(Normal)`, `GRPVER(10.0.0.0)`. (The remote Live
group still shows `GRPVER(9.4.5.0)` — that is expected at this stage; see the
next step.) `GRSTATUS(Normal)` must **never** be `Partitioned` (split-brain).

### 4.6 Confirm the mixed-version boundary

With the Recovery group on 10.0 and the Live group still on 9.4.5, the version
invariant (Recovery ≥ Live) holds, and CRR stays **connected across the version
boundary**. This is the expected, correct intermediate state — the Live group
keeps serving applications on 9.4.5 while you prepare its window. Confirm it from
the Recovery group:

```bash
dspmq -m <QM> -o nativeha -g
```

```text
QMNAME(<QM>)  GRPNAME(recovery)  GRPROLE(Recovery)  GRSTATUS(Normal)  GRPVER(10.0.0.0)
QMNAME(<QM>)  GRPNAME(live)      GRPROLE(Live)      GRSTATUS(Normal)  GRPVER(9.4.5.0)   CONNGRP(yes)
```

Assert: remote (Live) `CONNGRP(yes)` — the groups can still see each other across
the version boundary — and both `GRSTATUS(Normal)`. `GRPVER` differs by design
(Recovery `10.0.0.0`, Live `9.4.5.0`). Do not proceed to the Live group unless
`CONNGRP(yes)` and both groups are `Normal`.

### 4.7 Live group — repeat the upgrade

Now upgrade the Live group by exactly the same five steps. **Applications lose
service the moment you quiesce the Live group**, so this is where the
application-facing outage begins.

#### 4.7.1 Quiesce the Live group

This is the real quiesce — stop message flow at the edges (run MQSC against the
active instance):

```mqsc
STOP LISTENER(<LISTENER>)
```

```text
     1 : STOP LISTENER(<LISTENER>)
AMQ8005I: IBM MQ listener stopped.
```

Stop any running channels, then confirm none is still active or in-doubt:

```mqsc
DISPLAY CHSTATUS(*) STATUS INDOUBT
```

Assert: every channel is `STATUS(INACTIVE)` or `STATUS(STOPPED)` and none reports
`INDOUBT(YES)`. Stop or disconnect the applications, then confirm no application
connections remain:

```mqsc
DISPLAY CONN(*) TYPE(CONN) WHERE(APPLTYPE NE SYSTEM)
```

Expected: no connections are listed. Persistent messages still in the queues are
expected and will ride through the migration; non-persistent messages will not
survive the restart. Do not empty the queues.

#### 4.7.2 Back up each instance

Same as [4.2](#42-recovery-group--back-up-each-instance), on `live-1/2/3` with
the instance stopped:

```bash
tar -cvf /backup/<QM>-live-1-preupgrade.tar /var/mqm/qmgrs/<QM> /var/mqm/log/<QM>
cp /var/mqm/qmgrs/<QM>/qm.ini /backup/<QM>-live-1-qm.ini
cp /var/mqm/mqs.ini          /backup/<QM>-live-1-mqs.ini
```

#### 4.7.3 Stop the instances (and mqweb)

Same as [4.3](#43-recovery-group--stop-the-instances-and-mqweb), on all three
Live nodes:

```bash
endmqm -w <QM>        # (or: systemctl stop mqmonitor@<QM>) on live-1, live-2, live-3
endmqweb              # (or: systemctl stop mqweb) on each node
ps -ef | grep /opt/mqm | grep -v grep     # expect no output
```

#### 4.7.4 Upgrade the packages

Same as [4.4](#44-recovery-group--upgrade-the-packages), on all three Live nodes:

```bash
./mqlicense.sh -text_only
dnf -y upgrade 'MQSeries*'
dspmqver -b -f 2                          # expect: 10.0.0.0
```

#### 4.7.5 Start under 10.0 and verify

> **Point of no return for the Live group** — the first 10.0 start migrates its
> data one-way.

Start on all three Live nodes and verify. In the **Live** group the elected
instance reports `ROLE(Active)`:

```bash
strmqm <QM>           # on live-1, live-2, live-3 (or start the monitor unit)
dspmq -m <QM> -o nativeha -x
```

```text
QMNAME(<QM>)                                  STATUS(Running)
  INSTANCE(live-1) ROLE(Active)  INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
  INSTANCE(live-2) ROLE(Replica) INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
  INSTANCE(live-3) ROLE(Replica) INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) BACKLOG(0)
```

Assert: `QUORUM(3/3)`, one `ROLE(Active)`, all `INSYNC(yes) HASTATUS(Normal)`.
Both groups are now on 10.0 — proceed to [Validate and go-live](#validate-and-go-live).

---

## Back-out plan

Read this **before** the first 10.0 start. The realistic protection for a CRR
upgrade is the **staging** (Recovery group first), not a filesystem backup.

### The reality: no in-place downgrade once started on 10.0

The first `strmqm` under 10.0 migrates the queue-manager data in place, one-way.
IBM is explicit ([Migrating on AIX and Linux: single-stage][single-stage]):

> "After you start a queue manager, you cannot revert to the previous version.
> If you must restore the system, you cannot recover any work, such as changes to
> messages and objects, performed by the later version of IBM MQ."

and later on the same page:

> "At this point, queue manager data is migrated and you cannot revert to a
> previous release."

There is no clean downgrade after that point. You either **recover forward** (fix
on 10.0), **rebuild** the queue manager from scratch (clean, but loses queued
messages), or **restore a pre-upgrade backup** (which brings queued messages back
but discards all work done under 10.0 — see the hazard below).

### Filesystem backup: sanctioned for a standalone QM, not for Native HA

For a **standalone** queue manager, a filesystem backup **is** sanctioned. With
the queue manager stopped, copy the data directory **and** the log directory
**together** (all-or-nothing so the set is consistent), preserving ownership with
`tar`, and also back up `qm.ini`/`mqs.ini` before the upgrade
([Backing up queue manager data][bkp], [Restoring queue manager data][rst],
parent: [Backing up and restoring IBM MQ queue manager data][bkp-parent]). IBM:

> "Take copies of all the queue manager's data and log file directories,
> including all subdirectories… Preserve the ownerships of the files. For IBM MQ
> for UNIX and Linux systems, you can do this with the `tar` command… Before you
> upgrade to a later version of IBM MQ, take a backup of the `qm.ini` file and the
> registry entries." ([Backing up queue manager data][bkp])

**But there is no documented Native HA backup procedure.** In Native HA the
recovery log is Raft-replicated across the three instances, so a single-node
`tar` is **not** a sanctioned Native HA backup — it is a point-in-time copy of one
instance's files, not a consistent capture of the replicated group. If such a
file backup were restored, it would come back as a **standalone** queue manager
that must then be re-formed into a Native HA group. (That last point is an
inference extended from an RDQM/appliance-scoped restore statement
[[Restoring a queue manager — IBM MQ Appliance][appliance-restore]], **not** a
Native HA document — treat it as reasoning, not a documented Native HA
guarantee.)

### The IBM-sanctioned risk containment: the staging itself

The real, supported way to contain risk for a CRR upgrade is the ordering this
guide follows — **upgrade the Recovery group first**, and (optionally) plan to
switch over onto it. While the Recovery group is on 10.0 but the **Live group is
still 9.4.5 and untouched**, the back-out is trivial: **stop and do not proceed**.
The Live group keeps serving applications on 9.4.5; nothing on it has migrated.
([Upgrading Native HA CRR configurations][crr-upg],
[Complete a planned switchover on a Native HA CRR configuration][switchover].)

One constraint on this: you can only switch or fail over to a group at an
**equal-or-higher** version, so switching the Live role onto the upgraded (10.0)
Recovery group is **not** a downgrade path back to 9.4.5 — it is a forward move.
Staying wholly on 9.4.5 is possible only until the **first 10.0 start** on the
group you still care about.

### After the point of no return

Once a group has started on 10.0 there is no clean downgrade. If you must revert:

- **Rebuild from scratch** — recreate the queue manager and re-form the Native HA
  group. Clean, but **loses queued messages**.
- **Restore a pre-upgrade backup** — brings queued messages back, but **discards
  all work performed under 10.0**. This has a sharp hazard: any message that was
  **consumed** after the backup was taken **reappears** (it is back in the queue),
  and any message that was **produced** after the backup is **lost**. IBM
  acknowledges this loss of post-backup work
  ([Migrating on AIX and Linux: single-stage][single-stage], quoted above).

**Recommendation.** Take the standard pre-upgrade backup as due diligence, but
treat **rebuild** or **forward-fix on 10.0** as the realistic recovery. Do **not**
present the single-node `tar` as a safe Native HA rollback — it is not.

---

## Validate and go-live

Do this only after **both** groups are on 10.0.

### Health, quorum, and CRR

From each group, confirm the instances and the groups are healthy:

```bash
dspmq -m <QM> -o nativeha -x
```

Assert per group: `QUORUM(3/3)`, one elected instance (`ROLE(Active)` in the Live
group, `ROLE(Leader)` in the Recovery group), all `INSYNC(yes) HASTATUS(Normal)`.

```bash
dspmq -m <QM> -o nativeha -g
```

```text
QMNAME(<QM>)  GRPNAME(live)      GRPROLE(Live)      GRSTATUS(Normal)  GRPVER(10.0.0.0)
QMNAME(<QM>)  GRPNAME(recovery)  GRPROLE(Recovery)  GRSTATUS(Normal)  GRPVER(10.0.0.0)  CONNGRP(yes)
```

Assert: both groups `GRSTATUS(Normal)` (never `Partitioned`), both
`GRPVER(10.0.0.0)`, and the remote group `CONNGRP(yes)`.

### DR proof — planned switchover round trip

Prove the DR posture with a **planned switchover** (the reversible, zero-loss
path — not an unplanned failover) and then switch back. IBM: *"When you perform a
planned switchover, the system takes steps to ensure that you do not lose data or
encounter a partitioned (split-brain) problem… you can switch back to the
original live group by repeating the process."*
([Complete a planned switchover on a Native HA CRR configuration][switchover])

On the VM/RHEL arm the switchover is driven directly through `qm.ini`. On **every
instance of both groups**, edit two stanzas:

1. In the `NativeHALocalInstance` stanza, flip `GroupRole` — set the current Live
   group's instances to `Recovery`, and the current Recovery group's instances to
   `Live`:

   ```ini
   NativeHALocalInstance:
      GroupRole=Recovery      # on the group that is currently Live
   ```

   ```ini
   NativeHALocalInstance:
      GroupRole=Live          # on the group that is currently Recovery
   ```

2. In the `NativeHARecoveryGroup` stanza, set `Enabled` so the **new live side
   replicates outward** to the new recovery side:

   ```ini
   NativeHARecoveryGroup:
      Name=<remote group name>
      Enabled=True            # on the new live group, so it replicates to the new recovery group
   ```

Then restart the instances of each group (`endmqm -w <QM>` / `strmqm <QM>` on each
node, or bounce the monitor unit). Watch the roles pass through the pending states
and settle, then verify the switch:

```bash
dspmq -m <QM> -o nativeha -g
```

Assert: the roles have **swapped** (`GRPROLE(Live)` now on the former Recovery
group, `GRPROLE(Recovery)` on the former Live group), both `GRSTATUS(Normal)`
(**not** `Partitioned`), and the remote group `CONNGRP(yes)`. Confirm applications
reconnect to the newly active instance (Native HA has no floating VIP — clients
resolve the active instance) and message flow resumes.

**Switch back** by repeating the process in the other direction, restoring the
original Live/Recovery role assignment. Re-assert the same gates. If anything
shows `GRSTATUS(Partitioned)`, stop and resolve the split-brain before going live.

### Functional sanity — put/get

Confirm a **persistent** message survives end to end. Define a test queue with
persistent-by-default so `amqsput` produces persistent messages:

```mqsc
DEFINE QLOCAL(TEST.UPGRADE.Q) DEFPSIST(YES)
```

```bash
amqsput TEST.UPGRADE.Q <QM>
```

```text
Sample AMQSPUT0 start
target queue is TEST.UPGRADE.Q
upgrade sanity check
                                  <- press Enter on an empty line to end
Sample AMQSPUT0 end
```

```bash
amqsget TEST.UPGRADE.Q <QM>
```

```text
Sample AMQSGET0 start
message <upgrade sanity check>
no more messages
Sample AMQSGET0 end
```

Delete the test queue:

```mqsc
DELETE QLOCAL(TEST.UPGRADE.Q)
```

### Go live

Re-enable the edge and resume traffic on the Live group:

```mqsc
START LISTENER(<LISTENER>)
```

```text
     1 : START LISTENER(<LISTENER>)
AMQ8021I: Request to start IBM MQ listener accepted.
```

Restart any channels you stopped during quiesce, then resume the applications.
Confirm application connections re-establish (`DISPLAY CONN(*) TYPE(CONN)
WHERE(APPLTYPE NE SYSTEM)` now lists them) and check the queue manager error logs
are clean. The upgrade is complete: both groups on 10.0.0.0, CRR connected, DR
round trip proven.

---

## Sources

All pages are IBM Docs for IBM MQ 10.0.x unless noted.

- [Upgrading Native HA CRR configurations][crr-upg]
- [Complete a planned switchover on a Native HA CRR configuration][switchover]
- [Upgrading an IBM MQ installation on Linux Red Hat using dnf][dnf-upg]
- [`dspmq` — display queue managers (reference)][dspmq]
- [Migrating on AIX and Linux: single-stage][single-stage]
- [Backing up and restoring IBM MQ queue manager data][bkp-parent]
- [Backing up queue manager data][bkp]
- [Restoring queue manager data][rst]
- [Restoring a queue manager — IBM MQ Appliance 9.4.x][appliance-restore]
  (RDQM/appliance-scoped; cited only for the standalone-restore inference, not a
  Native HA guarantee)

[crr-upg]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=crr-upgrading-native-ha-configurations
[switchover]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=failover-complete-planned-switchover-native-ha-crr-configuration
[dnf-upg]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=umil-upgrading-mq-installation-linux-red-hat-using-dnf
[dspmq]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=reference-dspmq-display-queue-managers
[single-stage]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=linux-migrating-aix-single-stage
[bkp-parent]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=restart-backing-up-restoring-mq-queue-manager-data
[bkp]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=data-backing-up-queue-manager
[rst]: https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=data-restoring-queue-manager
[appliance-restore]: https://www.ibm.com/docs/en/mq-appliance/9.4.x?topic=restore-restoring-queue-manager
