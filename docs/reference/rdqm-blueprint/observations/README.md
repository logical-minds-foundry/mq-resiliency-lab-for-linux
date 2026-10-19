# RDQM black-box observations: HA under a DR outage, and a returning old primary (#1666)

This directory records what IBM RDQM **does** on a live `rdqm-rhel` HA/DR group, seen
from outside, in seven scenarios. It is the evidence the pcmk-drbd DR fix (#1660) and the
HA-quorum-loss fix (#1668) are designed from. The static configuration is the
[blueprint](../README.md) (#1425); this directory adds the behaviour.

Every claim is labelled:

- **Data**: what a recorded file shows. It cites `<scenario>/<file>:<line>`, relative
  to this directory.
- **Judgment**: reasoning on top of the data, about what IBM's compiled binaries do.

## 0. Provenance, method, licence, redaction

### 0.1 The stack

- **Cold build:** `mqlab teardown rdqm-rhel` at 2026-10-19T18:10:08Z, then
  `mqlab bootstrap rdqm-rhel` (both sites) from 18:10:14Z, finished 18:39:02Z with every
  phase green. This ran on the x86_64 cloud instance with nested KVM.
- **Healthy before S0:**
  - `RDQMAPP` was Running on `rdqm-a1` with `DR status: Normal`.
  - Site B was the DR secondary.
  - `mqlab qm e2e rdqm-rhel` passed 5/5.
- **Versions:** MQ 10.0.1.0 and the LINBIT cluster stack, as in the blueprint §0. The
  re-capture in S0 confirms the 2026-10-16 blueprint (§S0).
- **Clocks:** all nodes run `Timezone=UTC`. chrony reported every node within 1.2 ms of
  NTP at the start and end of each scenario (`<scenario>/clocks-*.txt`). Every time
  below is UTC.

### 0.2 Licence: black-box only

- No IBM binary was disassembled, traced, run through `strings`, or copied. The sources
  are the running system's own outputs:
  - the Pacemaker CIB and `pacemaker.log` (which names each CIB client);
  - DRBD state, from `drbdsetup events2` and the kernel log;
  - the journal;
  - command output;
  - the MQ error logs.
- **`/var/mqm/errors/RDQM.0.LOG`** is a log file that RDQM itself writes. It records each
  external command RDQM runs, as `---{ <command>` and `---} rc=… <command>`. Reading it is
  observation, like reading `pacemaker.log`, and it is the main source for "which IBM
  component changed what". The file is committed trimmed to each scenario window, as
  `<scenario>/<node>/RDQM.0.LOG`.

### 0.3 The recording kit

The kit is in [`kit/`](kit/):

- `obs-kit.sh`: journal prep, clocks, the `events2` recorders, step snapshots, the 5 s
  poller, operator-action marks, and collection.
- `timeline.py`, `cibattrs.py` and `cibwriters.py`: the analysis tools.
- `export.py`: produces the committed form.
- `drivers/`: the scenario scripts, as run, with the session paths turned into
  `MQLAB_REPO` and `OUT_ROOT`.

The kit departs from the issue's recording kit in five ways:

1. **Persistent journal.** `/var/log/journal` was missing on all six nodes, so the kit
   created it at 18:39:32–34 (`S0/journal-prep.log:1-12`). That is the one change made to
   the nodes.
2. **Journal sync.** journald syncs to disk only every 5 minutes by default, so in S4 the
   three destroyed site-A nodes lost their whole pre-crash boot
   (`S4/rdqm-a1/journal-boot-1.txt` is empty). From S1 on, every 5 s poll also runs
   `journalctl --sync; sync`, which loses at most about 5 s. S1's destroyed `rdqm-a1`
   kept 298 pre-crash lines in the raw recording.
3. **The poller samples all six nodes**, not only each site's HA primary. This is a
   superset of what the issue asks.
4. **`net-cut.sh` cannot boot a domain with its link already cut.** S3 used
   `virsh start --paused`, then `domif-setlink … down`, then `resume`
   (`S3/actions.log:40-62`). This is filed as #1673.
5. **S2 was continued by hand** after its scripted step-5 wait could not succeed (§S2).
   `drivers/drill-from6.sh` is the remainder that was run.

### 0.4 The committed form

`kit/export.py` produces it from the raw recordings:

- **Window trimming:** each timed source is cut to the scenario's `window`.
- **Noise removal, counted:** the kit's own SSH/sudo/session lines, the lab's
  observability agents and timers, the lab monitor's `AMQ8245W`/`AMQ8009W` authority
  warnings, and unchanged 5 s poll repeats are dropped. Each file's first line counts
  the dropped lines by category.
- **Compaction:** the journal's `ibm-mq[...]` JSON lines (MQ's copy of its error log) are
  compacted to `<message id> <message>`.
- **Redaction and guard:** DRBD `shared-secret` values and any PEM block are redacted.
  The export fails if `PRIVATE KEY` or an unredacted `shared-secret` survives.

**The raw recordings** are kept in the shared state bucket, at
`$(mqlab build path state)/rdqm-observe/20261019-issue-1666/` (766 MB).

### 0.5 Layout

| Path | Content |
|---|---|
| `<S>/actions.log` | every operator command, with UTC time, full output and rc |
| `<S>/snaps.log` | the step-boundary snapshots |
| `<S>/timeline.txt` | **the merged timeline**: every node and every source, sorted by UTC, as `<UTC> <node> <source> <text>`; poll lines are one-line digests, emitted on change |
| `<S>/<node>/journal.txt`, `journal-boot-1.txt` | journal for the window; `-b -1` is the pre-crash part of a node that was destroyed |
| `<S>/<node>/events2.log` | `drbdsetup events2 --timestamps --statistics all` |
| `<S>/<node>/pacemaker.log` | `/var/log/pacemaker/pacemaker.log` for the window, including the `cib_process_request` and `Diff:` lines |
| `<S>/<node>/cib-writers.txt` | every CIB change made by a client that is not a Pacemaker daemon, with its diff (`kit/cibwriters.py`) |
| `<S>/<node>/RDQM.0.LOG` | RDQM's command trace for the window |
| `<S>/<node>/amqerr/…` | MQ error logs for the window |
| `<S>/<node>/poll.log` | the 5 s poller output (`rdqmstatus -m RDQMAPP`, `crm_mon -1 -rA`, `drbdsetup status --verbose`), changes only |
| `<S>/<node>/snap/<NN>-<label>/` | `cib.xml` (`cibadmin -Q`, including status), `crm_mon.txt`, `drbd-status.txt`, `drbd-show.txt` (secrets redacted), `rdqmstatus*.txt`, `systemctl.txt`, `ls-var-mqm-rdqm.txt` |

The QM's own `AMQERR0*.LOG` on the replicated volume (`/var/mqm/vols/rdqmapp/…`) is in
`<S>/rdqm-a1/amqerr/var/mqm/vols/…` for S4, S1 and S1b. For S5, S2 and S3 it was fetched
after S3, at 20:21Z, because it had grown past the collector's 20 MB cap. It is the
replicated volume as it stands after S3's resolution, so it holds **site B's** history for
the S3 dual-active window. Site A's entries for that window were discarded with A's data;
the journal's `ibm-mq` lines (`S3/rdqm-a1/journal.txt`) keep them.

## 1. Summary

### 1.1 The answer to #1660's question

**Data.** RDQM keeps HA working while the DR link is down. It does **not** keep a
returning old primary from going live:

- **HA during a DR outage works, and nothing special happens.** With the WAN cut (S1) or
  the DR link broken gracefully (S1b), destroying the site-A HA primary moved `RDQMAPP`
  to `rdqm-a3` in **25.9 s** and **26.6 s** (`S1/rdqm-a3/journal.txt:157`,
  `S1b/rdqm-a3/journal.txt:202`). The new HA primary attached and promoted its own copy
  of `rdqmapp.dr` and started the QM, with the DR peer unreachable. No RDQM component
  wrote to the CIB to allow this. The only RDQM writes in the window are the `disconnected`
  handler's timestamp (`S1/rdqm-a1/RDQM.0.LOG:16`, `S1/rdqm-a2/cib-writers.txt`).
- **An isolated, restarted site A promotes its DR copy and starts `RDQMAPP`.** In S3, site
  A was destroyed after B's forced promote, then restarted with the WAN still cut. It
  started `RDQMAPP` at 19:47:22.4 (`S3/rdqm-a1/journal.txt:1783`) while B was also live.
  Both sites ran the QM as DR primary for **about 15½ minutes**, from 19:47:22 until the
  operator's `endmqm` at 20:02:53 (§S3). The returning A **made no RDQM CIB write** in that
  time (`S3/rdqm-a2/cib-writers.txt`). Nothing in the CIB stops it.
- **At reconnect, DRBD detects the split brain and leaves both sides running.** In S3,
  19:52:18, both sides logged `Split-Brain detected but unresolved, dropping connection!`
  (`S3/rdqm-a1/journal.txt:1900`, `S3/rdqm-b1/journal.txt:287`). Both went StandAlone.
  `rdqmstatus` reported **`DR status: Partitioned`** on both
  (`S3/rdqm-a1/poll.log:5622`, `S3/rdqm-b1/poll.log:5208`). Both QMs kept running for
  the remaining 10 minutes (`S3/S3.run.log:333-433`). RDQM stops neither.
- **The partition is resolved only by the operator.** IBM's procedure (`endmqm` on both,
  `rdqmdr -s` on the losing side, `rdqmdr -p` on the winning side) resolves it in
  seconds. `rdqmdr -s` runs **`drbdadm connect --discard-my-data rdqmapp.dr`**
  (`S3/rdqm-a1/RDQM.0.LOG:450`). `rdqmdr -p` runs **`drbdadm connect rdqmapp.dr`**
  (`S3/rdqm-b1/RDQM.0.LOG:192`). The kernel then logged `Split-Brain detected, manually
  solved. Sync from peer node` (`S3/rdqm-a1/journal.txt:2107`).
- **A returning site A promotes before the DR handshake, as on pcmk-drbd.** This happened
  in S4 (DR link up), S2 and S3. For example, in S4 `rdqmapp.dr` went Primary at
  18:44:43.091 and connected at 18:44:43.429 (`S4/rdqm-a1/journal.txt:1682`, `:1723`).
  That is the shape #1430 drill 6 found on pcmk-drbd. It is harmless only when the peer
  was never promoted (S4, S2).

**Judgment.** RDQM has **no mechanism that stops a returning old primary** before it
reconnects. IBM's documentation says as much:

- "when the main site recovers, you should try to ensure that the queue manager does not
  restart and run as a primary instance. If this is not possible, block the replication
  traffic between the two instances until you can restore the correct primary/secondary
  relationship" (IBM MQ 10.0, *Switching over to a recovery node if a disaster occurs*,
  <https://www.ibm.com/docs/en/ibm-mq/10.0.x?topic=environment-switching-over-recovery-node-if-disaster-occurs>,
  cached as `refs/ibm-docs/ibm-mq/10.0.x/environment-switching-over-recovery-node-if-disaster-occurs/content.txt`).
- The floor IBM documents for this case ("the queue manager on that node will be
  initially made the secondary", *RDQM disaster recovery*, `SSYHRD_10.0.0/configure/q131670_`)
  was **not** met by RDQM on its own in S3. In S2 it was met only because B never ran.
- What RDQM provides is **detection and an operator procedure**: DRBD's split-brain
  detection, the `Partitioned` status, and `rdqmdr -s`/`-p`, which wrap DRBD's manual
  split-brain recovery.

So a pcmk-drbd that matches RDQM needs no automatic guard against a returning old
primary, but it must:

- keep HA failover working while the DR link is down, as RDQM does;
- detect and report a partition;
- offer the same two-command resolution.

Anything beyond that (preventing the dual-active state) would be **stricter than RDQM**,
and is welcome under the ledger's rule.

### 1.2 The mechanism RDQM appears to use

**(a) Keeping HA failover working while the DR link is down**

| Element | What is seen (data) | Source |
|---|---|---|
| DR quorum on the live DR primary | Ungraceful WAN cut: `rdqmapp.dr` stays `quorum:yes`, `DR status: Remote unavailable`, QM Running | `S1/rdqm-a1/poll.log:402`, `:481`; `S3/rdqm-a1/poll.log:304`, `:383` |
| HA failover target attaches DR alone | `disk( Attaching -> UpToDate ) quorum( no -> yes ) [attach]`, then Primary, with no DR peer | `S1/rdqm-a3/journal.txt:89`, `:107`; `S1b/rdqm-a3/journal.txt:89`, `:105` |
| "Restored quorum from before reboot" | Logged on S1's HA failover node, and also on S3's isolated returning site and S5's no-reboot reattach (§1.4) | §1.4 |
| RDQM CIB writes needed for it | None. The DR clone keeps `target-role=Promoted`; only the `rdqmapp-dr-disconnect-time` timestamp is written | `S1/rdqm-a2/cib-writers.txt` |
| Graceful break (S1b) | `drbdadm disconnect` is local to the old primary's DR instance. After failover, a3's fresh `rdqmapp.dr` **reconnected to B and resynced**, so the failover undid the break | `S1b/rdqm-a3/journal.txt:151` |

**(b) A returning old primary after a forced DR**

| Step | What is seen (data) | Source |
|---|---|---|
| Forced promote at B, `rdqmdr -m RDQMAPP -p` | Runs `drbdadm cstate rdqmapp.dr`, then `drbdadm dstate rdqmapp.dr`, then `crm resource meta ms_drbd_dr_rdqmapp set target-role Promoted`. Nothing at the DRBD level, and **no change to `rdqmapp-dr-role`** | `S2/rdqm-b1/RDQM.0.LOG:16-40`; `S3/rdqm-b1/RDQM.0.LOG:43`; §2.3 |
| Promote of an Outdated copy | After a **graceful** break B is `disk:Outdated` and has no `master-p_drbd_dr_rdqmapp` score, so Pacemaker leaves it Unpromoted. **The QM never starts at B** | `S2/rdqm-b1/journal.txt:6`, `:22`; `S2/rdqm-b1/poll.log:282` |
| Returning site A | Promotes its DR copy from its static `target-role=Promoted`, before any handshake. RDQM writes nothing to stop it | `S3/rdqm-a1/journal.txt:1733`; `S3/rdqm-a2/cib-writers.txt` |
| Reconnect | DRBD split brain, unresolved; both StandAlone; `DR status: Partitioned`; both QMs keep running | `S3/rdqm-a1/journal.txt:1900`; `S3/rdqm-a1/poll.log:5622` |
| Resolution | Operator: `endmqm` ×2, then `rdqmdr -s` (`connect --discard-my-data`), then `rdqmdr -p` (`connect`) | `S3/actions.log:109-150`; `S3/rdqm-a1/RDQM.0.LOG:450`; `S3/rdqm-b1/RDQM.0.LOG:192` |

### 1.3 Every CIB element RDQM writes, and when

**Data**, from `RDQM.0.LOG` (the command) and `cib-writers.txt` (the CIB client and the
diff) in every scenario:

| CIB element | Written by (RDQM.0.LOG command → CIB client) | When |
|---|---|---|
| transient `rdqm-transient-attribute=1` (per node) | `rdqmd -s` (`rdqm.service`, boot): `attrd_updater --name rdqm-transient-attribute --update 1`. **Or** the alert agent `rdqmalert`, whichever comes first → `pacemaker-attrd` | Each boot, once the node's cluster is up. `rdqmd -s` first polls `crm status bynode` every ~1.5 s (`S4/rdqm-a1/RDQM.0.LOG:2-288`; `S4/rdqm-a3/journal.txt:1481` for `rdqmalert`) |
| transient `master-p_drbd_<qm>` **deleted** on the local node | `quorum-lost` handler (HA resource): `crm_attribute --type status --node <self> --name master-p_drbd_rdqmapp --delete` | HA quorum loss (S5: `S5/rdqm-a1/RDQM.0.LOG:40`; applied by attrd at 19:09:41.736 when membership re-formed, `S5/rdqm-a1/journal.txt:110`) |
| `crm_config rdqm: rdqmapp-dr-disconnect-time` | `disconnected` handler on `rdqmapp.dr` (both sides): `crm_attribute --set-name rdqm --name rdqmapp-dr-disconnect-time --update <UTC>` | **Written** on an ungraceful DR loss (S1 18:52:55 on both sides, `S1/rdqm-a1/RDQM.0.LOG:16`; S4 18:43:03 on B, `S4/rdqm-b2/cib-writers.txt`) and when Pacemaker stops the DR clone for a QM move (S1 18:57:32, S1b 19:05:31, S5 19:10:08). **Not written** on an administrative `drbdadm disconnect` (S1b 19:01:14, S2 19:15:48) or at S3's split-brain disconnect (19:52:18), although the handler ran each time (`S1b/rdqm-a2/cib-writers.txt`, `S2/rdqm-a2/cib-writers.txt`, `S3/rdqm-a2/cib-writers.txt`) |
| `crm_config rdqm: rdqmapp-dr-before-resync-time` | `before-resync-source` (DR primary) and `before-resync-target` (DR secondary): `crm_attribute … --update <UTC>` | Start of every DR resync (`S4/rdqm-a1/RDQM.0.LOG`, `S4/rdqm-b1/RDQM.0.LOG`) |
| `crm_config rdqm: rdqmapp-dr-snapshot-node=1` | `before-resync-target`: set after `lvcreate -s`; `after-resync-target`: `--delete` | Set at resync start on the DR target, deleted at resync end (`S4/rdqm-b1/RDQM.0.LOG:48-67`) |
| `ms_drbd_dr_<qm>` `target-role` | `rdqmdr -p` / `-s`: `crm resource meta ms_drbd_dr_rdqmapp set target-role Promoted\|Unpromoted` → `crm_resource` | Operator DR switch, forced or managed (`S2/rdqm-b1/cib-writers.txt`, `S3/rdqm-a1/RDQM.0.LOG:435`) |
| `rdqmapp` `maintenance=true`; delete `rdqmapp_with_ip` and `rdqmapp_after_ip`; add `location p_rdqmx_rdqmapp_preferred_location 50: <node>` | `endmqm`: `crm resource maintenance rdqmapp on`, `crm configure delete …`, `crm configure location …` → `cibadmin` | `endmqm` on an RDQM QM ("Replicated data queue manager disabled"; `rdqmstatus` `HA control: Disabled`) (`S2/rdqm-a1/cib-writers.txt`, `S3/rdqm-a2/cib-writers.txt`) |
| the reverse of the row above | `strmqm`: `crm configure load update /var/mqm/errors/rdqm.rdqmapp.<pid>.config`, `crm configure delete p_rdqmx_rdqmapp_preferred_location` → `cibadmin` | `strmqm` ("Replicated data queue manager enabled") (`S2/rdqm-a1/cib-writers.txt`) |

The following are **never written** in any scenario:

- `rdqmapp-dr-role`: 1 at A, 2 at B throughout, across every role flip (§2.3).
- `rdqmapp-ha-state`.
- Any `rsc_location` that bans a site.
- Any `no-quorum-policy`.

LVM, which is outside the CIB but is the DR snapshot (`RDQM.0.LOG` on the DR target):

- `before-resync-target` runs `lvremove -f drbdpool/rdqmapp_00-before-resync`, then
  **`lvcreate -s -n rdqmapp_00-before-resync -L 3088M drbdpool/rdqmapp_00`**, a real
  LVM snapshot of the data LV taken at resync start.
- `after-resync-target` runs `lvremove` on that snapshot, then
  **`lvcreate --wipesignatures n -L 3088M -n rdqmapp_00-before-resync drbdpool`**, which
  re-creates the plain *reserve* LV (`S4/rdqm-b1/RDQM.0.LOG:51`; S1–S5 the same).
- **Judgment:** this explains the blueprint's §1 "plain LV, not open, no Origin". At
  steady state it is a space reservation, which becomes the snapshot during each resync.

### 1.4 "Restored quorum from before reboot" (for #1668)

**Data.** The kernel line `drbd rdqmapp.dr: Restored quorum from before reboot`, by case:

| Case | Line logged? | Source |
|---|---|---|
| S1: HA failover to a3, WAN cut (legitimate) | **yes**, 18:53:39.816 | `S1/rdqm-a3/journal.txt:90` |
| S1: a1 returns after the WAN came back | yes | `S1/rdqm-a1/journal.txt:1723` |
| S1b: HA failover to a3 after a graceful break | no (`quorum( no -> yes ) [attach]` only) | `S1b/rdqm-a3/journal.txt:89` |
| S1b: a1 returns | yes | `S1b/rdqm-a1/journal.txt:1731` |
| S5: a1 re-attaches DR **without any reboot** | yes | `S5/rdqm-a1/journal.txt:379` |
| S3: isolated site A returns after a forced DR (illegitimate) | **yes**, 19:47:20.566 | `S3/rdqm-a1/journal.txt:1716` |
| S2: site A returns after a graceful break | no | `S2/rdqm-a1/journal.txt:1716` |
| S4: site A cold restart, DR link up | no | `S4/rdqm-a1/journal.txt:1665` |

**Judgment.** This confirms #1668's point on RDQM as well. The persisted DRBD quorum
state is logged identically for a legitimate HA failover (S1) and for an isolated old
primary returning after a forced DR (S3). It cannot tell the two apart, and RDQM adds
nothing that does.

### 1.5 HA quorum loss on the HA primary (S5; for #1668)

**Data.** RDQM's sequence (§S5):

1. `quorum-lost` deletes the local HA promotion score.
2. The `rdqm` agent stops the QM: "requesting immediate shutdown due to loss of quorum".
3. Pacemaker moves the whole chain to another node.
4. The DR resource on the old node, which had gone `Diskless` on an I/O error, is
   **stopped**, and later **started fresh** when the QM moves back.

The QM was serving again **23 s after the cut and 8 s after the link returned**. No
operator step was needed.

**Judgment.** On pcmk-drbd the same stall left the QM down until someone ran
`drbdadm -S adjust` (#1668). RDQM recovers because its handler forces a Pacemaker
move, and the stop/start cycle of the DR clone re-attaches the disk.

## 2. Per scenario

### S0: static baseline (read-only)

**Run** at 18:39:32–18:42:30:

- persistent-journal prep (`S0/journal-prep.log`) and clocks;
- `ansible/capture-rdqm-blueprint.yml` into
  `$(mqlab build path state)/rdqm-blueprint/20261019T183943Z`, failed=0 on all 6 hosts;
- snapshot `00-baseline`;
- per node, `systemctl cat rdqm.service` (copyright comment omitted), unit enablement,
  `systemctl show rdqm.service`, `cat /var/mqm/rdqm.ini` and `crm configure show`
  (`S0/<node>/s0-extra.txt`).

**Do the attributes and properties differ between site A and site B?**

- **Data:**
  - **Transient attributes in the steady state:** on every node, `#feature-set`,
    `master-p_drbd_rdqmapp=10000`, and **`rdqm-transient-attribute=1`**. Also
    `master-p_drbd_dr_rdqmapp=10000` on each site's HA primary
    (`S0/rdqm-a1/cibattrs-baseline.txt:63-66`).
  - **Permanent node attributes:** `drbd-replication`, `mqm-uid`, `mqm-gid`, `mqvrmf`,
    `rdqmapp-name` and `standby=off`. `rdqmapp-dr-replication` is present on the two
    non-creating nodes of each site only (`S0/rdqm-a1/cibattrs-baseline.txt`).
  - **`crm_config`:** `cib-bootstrap-options` holds `stonith-enabled=false`,
    `have-watchdog=false` and `maintenance-mode=false`. The `rdqm:` set holds the
    blueprint's values plus `rdqmapp-local-san=encrypted.remote` and
    `rdqmapp-remote-san=encrypted.remote`.
  - **A versus B** (`S0/rdqm-b1/cibattrs-baseline.txt`):
    - `rdqmapp-dr-role` is 1 at A and 2 at B (`:22`).
    - The DR clone has `target-role=Promoted` at A and `Unpromoted` at B (`:58`).
    - The DR local and remote IP lists are swapped.
    - The preferred location is a1 at A and b1 at B.
    - B's `rdqmapp_after_ip` order omits `first-action`/`then-action`.
  - **The transient attribute's origin:** first set at 18:24:49, during cluster creation,
    through `pacemaker-attrd` (`S0/rdqm-a1/transient-attr-origin.txt:1`). At each boot it
    is set again (§S4).
  - **`rdqm.service`:** `Type=oneshot`, `RemainAfterExit=yes`,
    `ExecStart=/var/mqm/rdqm/rdqmd -s`, `After=network-online.target pacemaker.service`
    (`S0/rdqm-a1/s0-extra.txt:7-13`). Enablement: `rdqm enabled`, `pacemaker enabled`,
    `corosync disabled (active)`, `drbd disabled`, `mqweb enabled`, `tlshd enabled`
    (`:18-23`).
  - **The re-capture** differs from the committed 2026-10-16 blueprint only in instance
    values: timestamps, UUIDs, CPU, memory, certificate bodies and dates
    (`S0/blueprint-diff.txt`).

### S4: control, whole-site cold restart with the DR link up

**Run** (`S4/actions.log`):

- 18:42:49: `virsh destroy` all three site-A domains;
- 60 s wait;
- 18:43:52.8: `virsh start` all three;
- recorders restarted;
- observed until 18:49:34.

**Does site A come back as DR primary, and how soon is RDQMAPP running?**

- **Data:** yes.
  - 18:44:15.8: `rdqm.service` started (`S4/rdqm-a1/journal.txt:1387`).
  - 18:44:39.0: `rdqmd -s` set the transient attribute, after polling
    `crm status bynode` from 18:44:15.9 (`S4/rdqm-a1/RDQM.0.LOG:2`, `:288`).
  - 18:44:41.6: HA promote on a1.
  - 18:44:43.091: `rdqmapp.dr` Primary (`S4/rdqm-a1/journal.txt:1682`).
  - 18:44:43.429: DR connected, with `uuid_compare()=source-use-bitmap by
    rule=crashed-primary` (`:1720`, `:1723`).
  - 18:44:48.410: `RDQMAPP` started (AMQ8003I, `:1789`).
  - That is **55.6 s** from the `virsh start` command. B resynced 53 MB from A
    (crashed-primary). B's `before-resync-target` took an LVM snapshot, and its
    `after-resync-target` restored the reserve LV (`S4/rdqm-b1/RDQM.0.LOG:48-67`).

**What did `rdqmd -s` change at boot?**

- **Data:**
  - Only the transient `rdqm-transient-attribute`, through `attrd_updater`, and on a1
    only (`S4/rdqm-a1/RDQM.0.LOG:288`). On a2 and a3, `rdqmd -s` only queried it
    (`S4/rdqm-a2/RDQM.0.LOG:283`, `S4/rdqm-a3/RDQM.0.LOG:283`), because the alert
    agent `rdqmalert` had already set it at 18:44:18
    (`S4/rdqm-a3/journal.txt:1481`).
  - The before/after CIB diff of `crm_config` shows only
    `rdqmapp-dr-before-resync-time` changed, by the `before-resync-source` handler
    (`S4/rdqm-a2/cib-writers.txt`).
- **Judgment:** no CIB rule or constraint references `rdqm-transient-attribute` (none
  in any `cib.xml`). Whether the `rdqm`/`rdqmx` agents test it cannot be seen from
  outside. The pause before the first probes (18:44:17 → 18:44:39) matches Pacemaker's
  own startup delay as well as the attribute, so the data does not show that it gates
  anything.

### S1: HA failover at the DR primary site, DR link down (ungraceful)

**Run** (`S1/actions.log`):

- 18:52:42.7: `net-cut.sh down net-wan rdqm-a1 rdqm-a2 rdqm-a3`;
- 18:53:15.8: `virsh destroy lab_rdqm-a1` (the HA primary);
- 3 minutes observed;
- 18:56:25.2: `net-cut.sh up net-wan rdqm-a2 rdqm-a3` (a1 was not running);
- 18:56:50.5: `virsh start lab_rdqm-a1`;
- observed until 19:00:37.

**Does RDQMAPP restart on a surviving site-A node while the DR link is down?**

- **Data:** **yes.**
  - 18:52:55.5: the WAN loss was detected. The `disconnected` handler on
    `rdqmapp.dr` logged `NetworkFailure` and wrote `rdqmapp-dr-disconnect-time`
    (`S1/rdqm-a1/journal-boot-1.txt:9-19`; `S1/rdqm-a1/RDQM.0.LOG:16`). The QM kept
    running with `rdqmapp.dr quorum:yes` and `DR status: Remote unavailable`
    (`S1/rdqm-a1/poll.log:389`, `:402`, `:481`).
  - 18:53:28 and 18:53:32: after the destroy, the `disconnected` handler on the **HA**
    resource ran on a3 and a2 (`S1/rdqm-a3/journal.txt:24`).
  - 18:53:39.5: HA promote on a3 (`:71-72`).
  - 18:53:39.816: `rdqmapp.dr` attached, `quorum( no -> yes ) [attach]` and
    **`Restored quorum from before reboot`** (`:89-90`).
  - 18:53:40.1: `rdqmapp.dr` Primary (`:107`).
  - 18:53:41.653: QM started (`:157`). That is **25.9 s after the destroy**, with
    `DR status: Remote unavailable` (`S1/rdqm-a3/poll.log:263`, `:276`).

**What do the CIB and DRBD show?**

- **Data:**
  - The DR clone stays `target-role=Promoted`.
  - Pacemaker moves the chain (`S1/rdqm-a2/pacemaker.log`).
  - The only RDQM CIB writes are the `disconnected` timestamps
    (`S1/rdqm-a2/cib-writers.txt`).
  - On WAN restore, a3 resynced to B (`before-resync-time` 18:56:34). At 18:57:24 the
    QM moved back to a1 (`location 50` > stickiness 1), and a1's attach also logged the
    restore line (`S1/rdqm-a1/journal.txt:1723`).
- Post-health: HEALTHY, primary a1, e2e 5/5 (`S1/actions.log`).

### S1b: as S1, with the DR link broken gracefully

**Run** (`S1b/actions.log`):

- 19:01:14.1: `drbd-degrade.sh break rdqm-a1 rdqmapp.dr`
  (`drbdadm disconnect rdqmapp.dr` on a1);
- 19:01:48.8: `virsh destroy lab_rdqm-a1`;
- 3 minutes observed;
- 19:04:59.3: `virsh start lab_rdqm-a1`;
- observed until 19:08:42.

**Data:**

- The graceful disconnect marked the peer: a1 logged `pdsk( UpToDate -> Outdated )`, and
  B logged `disk( UpToDate -> Outdated )` (`S1b/rdqm-a1/journal-boot-1.txt:15-19`,
  `S1b/rdqm-b1/journal.txt:16`).
- HA failover to a3 at 19:02:13: `rdqmapp.dr` attached `quorum( no -> yes ) [attach]`
  **without** a "Restored quorum" line, went Primary, and **connected to B at
  19:02:14.047**, resyncing a3 → B (`S1b/rdqm-a3/journal.txt:89`, `:105`, `:151`).
- QM started at 19:02:15.447, **26.6 s after the destroy** (`:202`).

**Is the result different from S1?**

- **Data:** the floor holds in both. The difference is that in S1b the failover ended the
  DR outage. `drbdadm disconnect` acts on the instance on a1 only. a3's fresh instance
  connects by default, so the DR link was back 25 s after the destroy, and B became
  UpToDate again.

### S5: brief HA partition of the HA primary, DR link up (#1666 addendum, for #1668)

**HA network.**

- **Data:** `drbdsetup show rdqmapp` uses `172.16.1.31:7000` (enp8s0, libvirt
  `net-hb-a`). Corosync `ring0_addr` is on the same network
  (`S0/rdqm-a1/s0-extra.txt`, `crm configure show`; live check on a1 before S5). One cut
  of `net-hb-a` on the HA primary therefore cuts both HA DRBD and corosync. No variant can
  cut one without the other.

**Run** (`S5/actions.log`):

- 19:09:25.6: `net-cut.sh down net-hb-a rdqm-a1`;
- 19:09:40.7: `net-cut.sh up net-hb-a rdqm-a1` (15 s);
- 5 minutes observed, no intervention.

**Does the HA resource lose quorum on the HA primary?**

- **Data:** yes. At 19:09:38.735, a1 logged `drbd rdqmapp/0 drbd100: quorum( yes -> no )`
  (`S5/rdqm-a1/journal.txt:20`).
- Corosync declared the token lost at 19:09:33.457 but was still inside its 12.78 s
  consensus wait when the link returned. Membership never split: `Members[3]: 1 2 3` at
  19:09:41.735 (`:12`, `:108`).

**What does `quorum-lost` visibly do?**

- **Data:**
  - It ran at 19:09:38.798 and executed
    `crm_attribute --type status --node rdqm-a1 --name master-p_drbd_rdqmapp --delete`
    at 19:09:38.905 (`S5/rdqm-a1/RDQM.0.LOG:40-45`).
  - It started **`rdqmd -m RDQMAPP -d`**, which logged `Waiting for stacked resource
    'rdqmapp.dr' to be stopped` from 19:09:39.031, and `Stacked resource 'rdqmapp.dr'
    stopped` at 19:09:49.072 (`S5/rdqm-a1/journal.txt:76`, `:161`, `:226`).
  - The attribute delete reached the CIB at 19:09:41.736, once membership was back
    (`:110`).
  - The scheduler then planned the move: promote HA on a3 and move the DR clone to a3
    (`S5/rdqm-a2/journal.txt:53`, `:55`).
  - At 19:09:41.979 the `rdqm` agent logged `stop(rdqmapp) requesting immediate shutdown
    due to loss of quorum` (`S5/rdqm-a1/journal.txt:119`).
  - No reboot. No other CIB change by RDQM besides the DR timestamps
    (`S5/rdqm-a2/cib-writers.txt`).

**Does the stacked DR resource detach?**

- **Data:** yes, on a1:
  - 19:09:38.777: `disk( UpToDate -> Failed ) [local-io-error]`;
  - 19:09:38.829: `Failed -> Diskless`;
  - `quorum( yes -> no )` (`S5/rdqm-a1/journal.txt:30`, `:54`).
  - Pacemaker then stopped it on a1. When the QM moved back to a1 at 19:10:08 it
    attached fresh, `Diskless -> Attaching -> UpToDate`, with "Restored quorum from
    before reboot" (`:370`, `:379`).

**Does RDQMAPP come back on its own, and how soon?**

- **Data:** yes, with no operator step:
  - on a3 at 19:09:48.671, **23 s after the cut** and 8 s after the link returned
    (`S5/rdqm-a3/journal.txt:199`);
  - back on a1 at 19:10:10.263 (`S5/rdqm-a1/journal.txt:485`).
- Post-health: HEALTHY, primary a1, e2e 5/5.

### S2: forced DR after a graceful break, then site A returns (#1430 drill 6 for RDQM)

**Run** (`S2/actions.log`):

- 19:15:31.9: T0, tag `S2191531`;
- `DEFINE QLOCAL(DR.FORCE.DRILL) DEFPSIST(YES)`;
- 19:15:44.3: 5 `HEAD-<n>-S2191531` put on a1;
- 19:15:48.4: `drbd-degrade.sh break rdqm-a1 rdqmapp.dr`;
- 19:16:08.4: 5 `TAIL` put (depth 10);
- 19:16:16.7: `virsh destroy` of all of site A;
- 19:16:41.8: on rdqm-b1, `rdqmdr -m RDQMAPP -p`, then `strmqm RDQMAPP`, following IBM
  *Switching over to a recovery node if a disaster occurs* (cited in §1.1).

**Forced promote: exact output.**

- **Data:**
  - `rdqmdr -p`: "Queue manager 'RDQMAPP' has been made the DR primary on this node.",
    rc 0.
  - `strmqm`: `AMQ7151E: The request could not be processed as the file system for
    queue manager 'RDQMAPP' is unavailable.`, rc 81 (`S2/S2.run.log:166-172`).
- **The QM never started at B.**
  - B's `rdqmapp.dr` had gone `Outdated` at the graceful break
    (`S2/rdqm-b1/journal.txt:6`).
  - Its `master-p_drbd_dr_rdqmapp` was unset at 19:16:08.8 (`:22`).
  - After `rdqmdr -p` set `target-role=Promoted` (`S2/rdqm-b1/RDQM.0.LOG:40`;
    `pacemaker.log:66`), Pacemaker still left the clone Unpromoted. `rdqmstatus` showed
    `Status not available` (`S2/rdqm-b1/poll.log:282`).
  - The scripted wait was stopped after 11 minutes (`S2/actions.log:33`).

**Count and e2e at B.**

- **Data:**
  - **Count:** unobtainable. `amqsbcg` failed `MQCONNX … Reason:2059`, because no QM
    was running (`S2/amqsbcg-rdqm-b1-192821.txt:5`). The `HEAD=0 TAIL=0` printed by the
    script (`S2/S2.run.log:174`) is not a count.
  - **e2e:** 5/5 failed, `MQRC_HOST_NOT_AVAILABLE` (2538) (`S2/S2.run.log:184`).

**Start site A; no intervention for 10 minutes.**

- **Run:** 19:29:06.6 `virsh start` of all three.
- **Data:**
  - **Did A promote its DR copy, mount, and start RDQMAPP?** Yes.
    - 19:30:00.817: `rdqmapp.dr` Primary, before the handshake at 19:30:01.179
      (`S2/rdqm-a1/journal.txt:1735`, `:1781`).
    - B was Outdated, so DRBD chose A as source (`bitmap-self`, `:1778`).
    - 19:30:03.028: QM started (`:1838`).
  - **What did `rdqmstatus` report?**
    - A: `DR role: Primary`, `DR status: Normal`.
    - B: `DR role: Primary`, `DR status: Normal`, QM `Status not available`
      (`S2/S2.run.log:216-313`).
    - No `Partitioned`, because B never ran.
  - **Which component changed what?**
    - B's Pacemaker now had B UpToDate with `target-role=Promoted`. It **tried to
      promote B's DR clone**; DRBD refused while A was Primary, so the promote timed out
      after 90 s.
    - This repeated, at 19:31:38, 19:33:29, 19:35:20 and later
      (`S2/rdqm-b1/journal.txt:221`, `:485`, `:748`).
    - Each cycle tore down and re-made the DR connection, visible as
      `rdqmapp-dr-disconnect-time` then `before-resync-time` every ~110 s
      (`S2/rdqm-a2/cib-writers.txt`).
  - **Did A resync from B on its own?** No. A was the source throughout.

**Resolution.** IBM *Resolving a partitioned (split brain) problem in DR RDQM*
(`SSYHRD_10.0.0/configure/q133050_`), "keep the data from the queue manager on the main
node". This is the right side, because A had all 10 messages and B had never run.

- **Run** (`S2/actions.log:57-178`):
  - 19:40:41.9: `endmqm RDQMAPP` on a1;
  - 19:41:15.5: `rdqmdr -s` on b1 ("made the DR secondary");
  - 19:41:16.3: `rdqmdr -p` on a1 ("is already the DR primary");
  - 19:41:39.1: `strmqm RDQMAPP` on a1.
- **Result:** A holds `HEAD-1..5` and `TAIL-1..5` (`:167-176`). **RPO 0**, since B never
  served.
- B's DR clone settled `Unpromoted` by itself, leaving a Pacemaker fail-count of 6 (later
  7).
- **Step 9 (fail back) did not apply:** A was already the live DR primary.

### S3: forced DR after an ungraceful WAN loss; site A restarted with the WAN still cut

**Run** (`S3/actions.log`):

- 19:43:30.3: T0, tag `S3194330`, 5 HEAD put;
- 19:43:46.7: `net-cut.sh down net-wan` at site A;
- 5 TAIL put;
- 19:44:13.5: `virsh destroy` of all of site A;
- 19:44:38.6: `rdqmdr -m RDQMAPP -p` on b1 ("has been made the DR primary"), then
  `strmqm` (AMQ7151E again; Pacemaker started the QM at 19:44:40.9,
  `S3/rdqm-b1/journal.txt:107`).

**Count at B.**

- **Data:** for tag `S3194330`, **HEAD 5, TAIL 0** (`S3/amqsbcg-rdqm-b1-194451.txt`).
  RPO > 0 equals exactly the tail. The script's `HEAD=10 TAIL=5` (`S3/S3.run.log:220`) also
  counted S2's messages.

**e2e at B.**

- **Data:** 5/5 failed, `MQRC_NO_MSG_AVAILABLE` (2033). This is a lab topology limit, not
  RDQM:
  - site B has no `net-ext` link, so `RDQMAPP.SVCQM` at B logged `AMQ9202E: Remote host
    not available` (`S3/rdqm-b1/journal.txt:155`);
  - SVCQM's reply channel lists site-A hosts only (`S3/svc-channels.txt`).
  - Filed as #1675.

**Isolated return.**

- **Run:** 19:46:23–28: each site-A domain started `--paused`, its `net-wan` link set
  down, then resumed. 19:52:09.4: `net-cut.sh up net-wan`, then 10 minutes observed.

**Does an isolated, restarted site A promote its DR copy and start RDQMAPP?**

- **Data:** **yes.**
  - `rdqmd -s` ran at boot and only queried the transient attribute
    (`S3/rdqm-a1/RDQM.0.LOG`).
  - 19:47:20.566: `rdqmapp.dr` attached with "Restored quorum from before reboot"
    (`S3/rdqm-a1/journal.txt:1716`).
  - 19:47:20.868: Primary (`:1733`).
  - 19:47:22.407: QM started (`:1783`).
  - From then on, A and B both ran `RDQMAPP` as DR primary (`S3/rdqm-a1/poll.log`,
    `S3/rdqm-b1/poll.log`).
  - **Nothing in RDQM prevented the dual-active state.**

**What happens at reconnect?**

- **Data:**
  - 19:52:18.109: `uuid_compare()=split-brain-auto-recover by rule=bitmap-both`.
  - The DRBD `initial-split-brain` and `split-brain` helpers ran. These are plain
    `drbdadm` helpers, not `rdqmhandler`, and exited 0.
  - `Split-Brain detected but unresolved, dropping connection!` on both, then StandAlone
    (`S3/rdqm-a1/journal.txt:1897-1913`, `S3/rdqm-b1/journal.txt:287`).
  - `DR status: Partitioned` on both from 19:52:21 (`S3/rdqm-a1/poll.log:5622`,
    `S3/rdqm-b1/poll.log:5208`).
  - Both QMs were still running at 20:02:20, 10 minutes later (`S3/S3.run.log:333-433`).

**Partitioned state.**

- **Data:**
  - A: 20 messages, including `HEAD-1..5` and `TAIL-1..5` of `S3194330`
    (`S3/actions.log:86-97`).
  - B: 15 messages, with `HEAD-1..5` only (`:100-106`).

**Resolution.** IBM `q133050_`, "keep the data from the queue manager on the recovery
node", since B was the forced-DR live site. A's TAIL is discarded.

- **Run** (`S3/actions.log:109-158`):
  - 20:02:53.7: `endmqm` on a1;
  - 20:03:03.5: `endmqm` on b1;
  - 20:03:20.9: `rdqmdr -s` on a1, which ran `crm resource meta … Unpromoted` then
    `drbdadm connect --discard-my-data rdqmapp.dr` (`S3/rdqm-a1/RDQM.0.LOG:435`, `:450`);
  - 20:03:22.7: `rdqmdr -p` on b1, which ran `drbdadm connect rdqmapp.dr`
    (`S3/rdqm-b1/RDQM.0.LOG:192`);
  - the kernel logged `Split-Brain detected, manually solved` (`S3/rdqm-b1/journal.txt:539`);
  - 20:03:35: `DR status: Normal` on both;
  - 20:03:50.5: `strmqm` on b1.

**Fail back.**

- **Run:** 20:03:54.8: `mqlab dr failback rdqm-rhel --rpo0-drill`.
- **Data:**
  - It **exited 1** after a 180 s settle wait (`S3/failback.log`; `S3/actions.log:176`).
    The QM at a1 stayed `Ended immediately`, because the earlier `endmqm` had left
    `HA control: Disabled` and `rdqmapp` in `maintenance`. Filed as #1674.
  - 20:08:19.3: IBM's final step, `strmqm` on a1, brought it up: Running, DR Primary,
    Normal. The tool's RPO-0 token was retrieved, and A now holds B's data, `HEAD-1..5`
    (`S3/actions.log:201-222`).

**Downstream effect of the dual-active window.**

- **Data:** after the resolution, both RDQMAPP↔SVCQM channels failed with
  `AMQ9526E: Message sequence number error` (`S3/rdqm-a1/journal.txt:2253`). During the
  partition both copies of RDQMAPP had exchanged messages with SVCQM, and keeping B's
  data rolled RDQMAPP's channel state back.
- `RESET CHANNEL` on both senders restored the flow (§4). This is outside RDQM.

## 3. Gaps

1. **What the IBM agents check.** Whether `ocf:ibm:rdqm`, `ocf:ibm:rdqmx` or `rdqmd`
   read `rdqm-transient-attribute` or the `rdqm:` properties cannot be seen from outside.
   Their `RDQM.0.LOG` entries show `cibadmin --query` and `systemctl is-active` only.
2. **What `rdqmd -s` waits for.** It polls `crm status bynode` until a condition holds,
   but the output it parses is not logged.
3. **Pre-crash journal in S4.** The destroyed nodes' journals before 18:42:49 were lost
   (journald had not synced). The poller and the `events2` files cover the state.
4. **Who requested the attrd delete in S5.** `pacemaker-attrd` does not name the client.
   The attribution to `quorum-lost` rests on `RDQM.0.LOG:40` (same node, same attribute,
   2.8 s earlier, while the CIB was unreachable).
5. **Site A's QM error log for S3's dual-active window.** It lived on A's DR volume and
   was discarded by `--discard-my-data`. The journal's `ibm-mq` copy keeps the entries
   (`S3/rdqm-a1/journal.txt`).
6. **e2e at site B is impossible in this lab** (#1675). Whether B served applications
   during S3 is shown by `amqsbcg`/`runmqsc` at B, not by the e2e flow.
7. **RDQM's own split-brain helpers.** `initial-split-brain` and `split-brain` are not
   linked to `rdqmhandler` (`ls-var-mqm-rdqm.txt`). If RDQM reacts to a split brain, it
   does so only through `rdqmstatus` reporting `Partitioned`.

## 4. Final state of the lab

The lab is **healthy, in the S0 state**: `RDQMAPP` Running on rdqm-a1, `DR status:
Normal`, B the DR secondary, `mqlab qm e2e rdqm-rhel` 5/5. Getting there after S3 took
these steps, all recorded:

- `RESET CHANNEL` and `START CHANNEL` on `RDQMAPP.SVCQM` (RDQMAPP) and `SVCQM.RDQMAPP`
  (SVCQM) (`S3/actions.log:234-262`);
- `DELETE QLOCAL(DR.FORCE.DRILL) PURGE` and `DELETE QLOCAL(DR.RPO0.DRILL) PURGE`;
- `crm_resource --cleanup --resource ms_drbd_dr_rdqmapp --node rdqm-b1`, to clear the
  failed-promote record left by S2 ([`restore-actions.log`](restore-actions.log)).

`/var/log/journal` stays on all six nodes. The next rebuild removes it.

## 5. Follow-ups

Filed under epic logical-minds-foundry/.github#310:

- #1673: `net-cut.sh` cannot boot a domain with a link already cut (the S3 workaround).
- #1674: `rdqm-dr-cutover.sh` never restarts a queue manager left disabled by `endmqm`
  (S3 failback).
- #1675: `rdqm-rhel` site B cannot reach the service simulator, so e2e fails after a DR
  cutover.
