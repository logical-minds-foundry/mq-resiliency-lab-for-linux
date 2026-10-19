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
- [Replication TLS (pcmk-drbd arm)](#replication-tls-pcmk-drbd-arm)
- [Returning DR site after a forced DR](#returning-dr-site-after-a-forced-dr)
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
   message-path `qm-main`/`svc-sim`) was what finally let it reach
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

**Fix.** Check the live resource state first; `drbdadm status` **never blocks**.

**The second hang mode — an interactive prompt (#158).** Even on a **down**
device, `dump-md`/`create-md` can block — but in **S (interruptible)** state, not
D, on a **free** device — when they hit a confirmation prompt (residual/foreign
on-disk md) and Ansible supplies no stdin to answer. Tell them apart by `ps stat`
(`D` = exclusive-access, reboot to clear; `S+` = stdin prompt, killable) and by
`fuser /dev/vdb` (a free device rules out the in-use case). The hardened probe
closes stdin so a prompt EOFs and bails, and bounds every metadata op with a
`timeout` so it fails in seconds, never hours; `set -e` keeps a real `create-md`
failure from silently falling through to a success echo:
```sh
set -e
if drbdadm status mqlun >/dev/null 2>&1; then echo resource-up         # up: skip
elif timeout 30 drbdadm dump-md mqlun </dev/null >/dev/null 2>&1; then  # down, md present
  echo md-exists
else timeout 60 drbdadm create-md --force mqlun </dev/null; echo created  # down, fresh
fi
```

**Meta-lesson (→ #72).** A wedged sync and a slow sync look identical from the
outside; only a watchdog on log-progress tells them apart. Actively monitor long
operations — don't trust that a hang will announce itself. Corollary (#158): never
let a provisioning command read from an interactive stdin — close it and bound it.

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

## Replication TLS (pcmk-drbd arm)

The `pcmk-drbd-ubuntu` arm's two DRBD resources per QM set `tls yes;` as RDQM's do
(#1609). DRBD hands each connection's handshake to `tlshd` through the kernel's
`handshake` upcall, on both the control socket and the data socket, and asks it to verify
the peer under the peer's **configured name**: the host name on the HA mesh, and
`encrypted.remote` on the DR link. Both names are SANs in every node's certificate.

| Symptom | Cause | Fix |
|---|---|---|
| A peer never leaves `Connecting` | The TLS handshake fails | `journalctl -u tlshd` on both ends (it logs at level 1), and `dmesg` for `tls handshake` lines from `drbd_transport_tcp`. Check that `tlshd` is active and that the certificate's SANs carry the peer name |
| `drbdadm adjust` fails with `cannot switch tls (no -> yes) while connected` | The kernel refuses to change `tls` on a connection that has sockets (`drbd_transport_tcp.c` `dtt_net_conf_change`) | Rebuild the stack (`mqlab teardown`, then `mqlab bootstrap`). The provision stops on this before it renders any config |
| `drbdsetup status --verbose` shows `tls:yes`, but is the link encrypted? | `tls:` is the configured flag, not a socket property | On a `Connected` link it is: the flag cannot change while connected, and the connect only completes after the handshake. `/proc/net/tls_stat` (`TlsCurrTxSw`/`TlsCurrRxSw`) counts the kTLS sockets, two per connection. `site-pdrbd-tls-verify.yml` checks both |

## Returning DR site after a forced DR

This applies to the `pcmk-drbd-ubuntu` arm (#1660). A forced DR
(`site-pdrbd-dr-switch.yml -e dr_force=true`) never contacts the lost site. So
when that site comes back, its Pacemaker still has the DR clone at
`target-role=Promoted`.

Before #1660, the site promoted its DR resource at boot, before its first DRBD
handshake. That gave two live queue managers and a DR split brain (#1430
drill 6). The returning `ocf:linbit:drbd` member saw its peer as `Outdated`, a
*known* peer-disk state. DRBD 9 restores that state from the
`MDF_PEER_OUTDATED` metadata flag that a graceful `drbdadm disconnect` sets. So
the agent gave it its top score.

The DR clone now runs `ocf:pdrbd:drbd-dr`
(`ansible/roles/pdrbd-qm/files/drbd-dr.sh`). That is `ocf:linbit:drbd` with
one added rule: a DR member that is not Primary can be promoted only in two
cases.

- It is `Connected` to a peer in the `Secondary` role, and its own disk is
  `UpToDate`.
- The forced DR has granted it one promotion.

| Symptom | Cause | Fix |
|---|---|---|
| The returning site's DR clone stays `Unpromoted` while its `target-role` is `Promoted`. `mq_group` is Stopped there. The journal shows `pdrbd-drbd-dr[...]` `refusing to promote` or `started without a promotion score` | Working as designed. The other site is the DR primary. The returning member is `StandAlone` (split brain) or `Connected` to a Primary | `cd ansible && ansible-playbook site-pdrbd-dr-rejoin.yml -e rejoin_site=<a\|b> -e qm_name=<QM>`. This makes the returning site the DR secondary of record (`target-role=Unpromoted`, dr-role 2). If DRBD resynced it by itself, it waits for the resync. Then fail back with `mqlab dr failback\|cutover` |
| The rejoin stops with `SPLIT BRAIN` | Both sites wrote after the link broke, so the data diverged (IBM calls this "partitioned"). There is no `after-sb-*` policy (RP-7), as on RDQM | Decide which data to keep. To keep the live site's data, re-run with `-e dr_discard=true`. The returning member reconnects with `--discard-my-data` and resyncs from the live site. To keep the returning site's data, follow IBM's second procedure ("Resolving a partitioned (split brain) problem in DR RDQM"). First stop the live site's QM: `pcs resource meta drbd_dr_qm-clone target-role=Unpromoted --wait=300` there, and set its `pdrbd:<qm>-dr-role` to 2. Then run a forced DR toward the returning site. Then rejoin the other site with `-e dr_discard=true` |
| The rejoin stops with `BOTH sites run the DR Primary` | A dual-active partition, for example on a stack formed before #1660 | The same decision. `-e dr_discard=true` stops the returning site's QM and discards its data |
| After an HA failover at the DR primary site, the new HA primary's DR clone stays `Unpromoted` | The DR link was down during the failover. A member that is not Primary is promotable only while it is connected (the #1660 rule) | Restore the DR link: the next monitor (at most 20 s) promotes it. Or run a forced DR toward the same site |

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
