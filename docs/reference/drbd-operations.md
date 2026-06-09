# DRBD Operations Reference (lab Pacemaker/SAN arm)

> Hard-won, symptom-first. Host-based DRBD **protocol A (async)** replicates the
> SAN LUN's backing device `san-a` ↔ `san-b` over `net-wan`; LIO exports
> `/dev/drbd0` (not the raw disk), so the block storage itself replicates
> cross-site. Everything below was learned by getting it wrong during the #67
> forced-DR build.

## Contents

- [The initial-sync fight (250 KB/s → seconds)](#the-initial-sync-fight-250-kbs--seconds)
- [The dump-md hang (2-hour stall)](#the-dump-md-hang-2-hour-stall)
- [Force-promote on a dead peer](#force-promote-on-a-dead-peer)
- [Command quick-reference](#command-quick-reference)

## The initial-sync fight (250 KB/s → seconds)

**Symptom.** After bringing the resource up, `drbdadm status mqlun` shows
`replication:SyncSource peer-disk:Inconsistent done:9.77` creeping, and
`/proc/drbd` reads `speed: 248 K/sec`, `finish: 8:19:19`. An 8 GB device at
250 KB/s is an **8-hour** sync.

**What I tried, in order, and what each taught:**

1. `drbdadm disk-options --resync-rate=512M mqlun` — **no effect.** `drbdsetup
   show` confirmed the value *was* set (`resync-rate 524288k`), yet the live rate
   stayed ~250 KB/s. Lesson: the config being set ≠ the running resync honoring it.
2. Suspected the network. It is **not** the WAN: `net-wan.xml` has no `<bandwidth>`
   cap and `tc qdisc` shows only `fq_codel`. A raw `nc` transfer ran at GB/s.
   Lesson: rule out the network before blaming DRBD.
3. **Root cause #1 — net buffers.** Protocol A over a low-latency link needs
   generous buffers; the defaults throttle it. Added
   `net { max-buffers 80k; sndbuf-size 2M; rcvbuf-size 2M; }`.
4. **The unlock — restart the resync.** A running resync does **not** pick up new
   options. `drbdadm disconnect mqlun; sleep 1; drbdadm connect mqlun` restarts it
   *from a low %* but at the new rate — it jumped to **~50 MB/s**.
5. **Root cause #2 — `c-min-rate=0` + app I/O.** I had set `c-min-rate=0`, which
   lets application I/O (the running QM writing to the LUN) starve the resync.
   Either stop the QM (`pcs resource disable mq_group`) during the bulk sync or set
   a high `c-min-rate`.
6. **Root cause #3 — host I/O contention.** With 11 VMs on the host the sync is
   I/O-bound regardless of DRBD config. Halting the non-essential VMs (the
   message-path `qm-main`/`dtcc-sim`) was what finally let it reach
   `UpToDate/UpToDate`.
7. **The real fix — don't sync at all.** Both backing disks start **blank**, so a
   full 8 GB copy is pointless. `drbdadm new-current-uuid --clear-bitmap mqlun`
   (run on the primary while both peers are up) declares both UpToDate at zero;
   the `mkfs` + QM writes then replicate as ordinary protocol-A traffic. **Seconds,
   not minutes.** This is now the codified default (`drbd-san` role +
   `site-pcmk-dr.yml`'s post-both-up play).

**Codified.** `net { max-buffers/sndbuf/rcvbuf }` + `disk { c-plan-ahead 0;
resync-rate 500M }` are in the resource file as a safety net; skip-initial-sync
is the primary path. If you ever DO need a full resync to go fast, the recipe is:
stop app I/O, raise buffers + rate, then `disconnect`/`connect` to restart it.

## The dump-md hang (2-hour stall)

**Symptom.** A re-provision sat at `TASK [drbd-san : create metadata]` and its
log mtime stopped advancing — for **two hours**. `pgrep -af drbdadm` on the node
showed a stuck `drbdadm dump-md mqlun` and its child `drbdmeta`, both in **D
(uninterruptible)** state — `kill -9` does nothing.

**Root cause.** `drbdadm dump-md` / `create-md` read on-disk metadata via
`drbdmeta`, which needs **exclusive access to the device**. On an in-use
(Primary/up) device it blocks forever waiting for it. My idempotency probe was
`if drbdadm dump-md ...; then md-exists; else create-md; fi` — so re-running over
an already-up resource wedged.

**Fix.** Check the live resource state first; `drbdadm status` **never blocks**:
```sh
if drbdadm status mqlun >/dev/null 2>&1; then echo resource-up   # skip
elif drbdadm dump-md mqlun >/dev/null 2>&1; then echo md-exists   # down, md present
else drbdadm create-md --force mqlun; fi                          # down, fresh
```

**Meta-lesson (→ #72).** A wedged sync and a slow sync look identical from the
outside; only a watchdog on log-progress tells them apart. Actively monitor long
operations — don't trust that a hang will announce itself.

## Force-promote on a dead peer

After an abrupt full-site loss, on the survivor SAN:
```sh
drbdadm primary --force mqlun     # accept the unreplicated tail as lost
drbdadm role mqlun                # Primary/Unknown  <- correct; peer is gone
```
`Primary/Unknown` is the **healthy** forced-DR state (the `Unknown` is the dead
peer). The LUN under `/dev/drbd0` is then exportable via LIO.

**Timing trap.** Immediately after the kill, the survivor may still be
`Connecting` to the dead peer; a force-promote wrapped in `|| true` can silently
no-op and leave it `Secondary`. Always **verify `role:Primary`** before the next
cutover step (see the fail-loud check in `pcmk-dr-force.sh`).

## Command quick-reference

| Need | Command |
|---|---|
| State (never blocks) | `drbdadm status mqlun` · `drbdadm dstate mqlun` · `drbdadm role mqlun` |
| Live sync %/speed | `cat /proc/drbd` |
| Restart a resync at new options | `drbdadm disconnect mqlun; drbdadm connect mqlun` |
| Skip initial sync (blank disks, both up) | `drbdadm new-current-uuid --clear-bitmap mqlun` |
| Stop app I/O for a bulk sync | `pcs resource disable mq_group` (re-enable after) |
| Forced promote (peer dead) | `drbdadm primary --force mqlun` |
| Never use on an up device | `drbdadm dump-md` / `create-md` — they hang |
