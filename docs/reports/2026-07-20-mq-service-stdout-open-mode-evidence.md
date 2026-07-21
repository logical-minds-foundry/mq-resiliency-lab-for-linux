# Evidence: does an IBM MQ `SERVICE` truncate or append its `STDOUT` file on (re)start?

**Status:** definitive — resolved by direct lab test, two independent methods.
**Date:** 2026-07-20 · **Platform:** IBM MQ **9.4.5.0**, RHEL 9.6 (kernel
`5.14.0-570.12.1.el9_6`), throwaway queue managers, **circular** logging.

## Why this document exists

The how-to *"IBM MQ instrumentation events to a file, as JSON"* asserted, as a
"verified in the lab" claim, that **an MQ service truncates its `STDOUT` file on
every (re)start**, and built its entire design on that claim: a separate launcher
script that redirects `amqsevt` output to a data file with an explicit append
(`>>`), *specifically to avoid the truncation*. A later review produced the
**opposite** claim — that the service **appends** — also citing evidence. Two
contradictory "evidenced" conclusions is not acceptable for a document that shipped
to a client, so this test settles it with proof a human can inspect and re-run,
not a narrative to be trusted.

**If the service appends, the launcher is unnecessary and the design collapses.**
That is the stake.

## Method

Two **independent** proofs that cannot both be fooled by the same mistake:

1. **Content persistence.** Pre-seed the `STDOUT` file with a known sentinel line;
   start a minimal `SERVER` service that writes one unique marker to its stdout;
   inspect. Sentinel still present ⇒ **append**; gone ⇒ **truncate**. Then STOP →
   START again and check whether the accumulated content survives.
2. **Kernel open flags.** Read `/proc/<service-pid>/fdinfo/1` — the actual flags
   the kernel recorded for the service process's stdout. `O_APPEND` is octal
   `02000`. Its presence is not an inference about behaviour; it is the flag MQ
   passed to `open(2)`.

`inode` and `size` are captured at every step: a stable inode with growing size is
in-place append; truncation resets size to the new write and (typically) is visible
as content loss.

A third test then validates the **consequence** — that `amqsevt` can be run
**directly** as the service (no launcher) with `STDOUT` pointed straight at the
data file.

---

## Test 1 — `STDOUT` open mode (minimal probe)

### Exact script (`stdout-proof.sh`, run as `mqm`)

```bash
QM=EVTPROOF; D=/var/mqm/stdout-proof; PROBE=$D/probe.sh; OUT=$D/probe.out
crtmqm "$QM"; strmqm "$QM"                      # default => circular logging
# probe: write ONE unique marker to stdout, then sleep so we can read fdinfo
cat > "$PROBE" <<'EOS'
#!/bin/bash
echo "RUN_MARKER pid=$$ nonce=${RANDOM}${RANDOM} ts=$(date +%s.%N)"
exec sleep 600
EOS
chmod 0755 "$PROBE"
printf "DEFINE SERVICE(STDOUT.PROBE) REPLACE CONTROL(MANUAL) SERVTYPE(SERVER) \
STARTCMD('$PROBE') STDOUT('$OUT') STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')\n" | runmqsc "$QM"
printf 'SENTINEL_PRESEED_LINE_A\n' > "$OUT"           # pre-seed sentinel
# start, inspect content + /proc/<pid>/fdinfo/1 ; then STOP + START again
```

(Full script committed under this epic's branch; re-runnable verbatim.)

### Raw output (verbatim)

```text
################ ENVIRONMENT ################
9.4.5.0
Linux 5.14.0-570.12.1.el9_6.x86_64
... LOGTYPE(CIRCULAR)

################ TRIAL 1: pre-seed a SENTINEL, then start the service ################
[BEFORE start]
  stat: inode=254201 size=24
  --- content ---
  | SENTINEL_PRESEED_LINE_A
[AFTER start]
  stat: inode=254201 size=88
  --- content ---
  | SENTINEL_PRESEED_LINE_A
  | RUN_MARKER pid=1017558 nonce=1902422563 ts=1784558943.707396755
[OS-LEVEL PROOF — the flags MQ opened this stdout with]
  fd1 -> /proc/1017558/fd/1 -> /var/mqm/stdout-proof/probe.out
  fdinfo/1:
    pos:  88
    flags:  0102001
    ino:  254201
  raw open flags (octal) = 0102001
  O_APPEND(02000) set?  YES -> APPEND mode

################ TRIAL 2: RESTART ('truncates on every restart') ################
[BEFORE restart]
  stat: inode=254201 size=88
[AFTER restart]
  stat: inode=254201 size=152
  --- content ---
  | SENTINEL_PRESEED_LINE_A
  | RUN_MARKER pid=1017558 nonce=1902422563 ts=1784558943.707396755
  | RUN_MARKER pid=1017661 nonce=3151216155 ts=1784558949.796950470
```

### Reading

- **Content:** the sentinel survived the service start, and **both** run-markers
  survived the restart — the file only ever grew (24 → 88 → 152 bytes) at a
  **constant inode (254201)**. In-place append. No truncation.
- **Kernel flags:** `0102001` = `O_WRONLY | O_APPEND | O_LARGEFILE`. `O_APPEND`
  (`02000`) is **set**. MQ explicitly opened the file for append.

---

## Test 2 — the launcher is unnecessary (run `amqsevt` directly, `STDOUT` = data file)

### Exact service definition

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE CONTROL(QMGR) SERVTYPE(SERVER)
  STARTCMD('/opt/mqm/samp/bin/amqsevt') STARTARG('-m +QMNAME+ -o json')
  STDOUT('<data-file>')
  STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')
```

### Raw output (verbatim)

```text
collector PID = 1019444  (process: /opt/mqm/samp/bin/amqsevt -m EVTPROOF2 -o json )
amqsevt stdout (fd1) open flags: 0102001  (O_APPEND=02000)

FORCE EVENT 1 (Not Authorized 2035)
data file after event 1:  inode=34597343 size=11717
  "eventType" : {   (x3 shown)

RESTART, then check retention
data file right after STOP:            inode=34597343 size=12418
data file after restart:               inode=34597343 size=12418
  total eventType occurrences in file: 8
```

### Reading

- The service's process **is** `amqsevt -m EVTPROOF2 -o json` — MQ word-splits
  `STARTARG` and expands `+QMNAME+`; no launcher needed to shape the command.
- `amqsevt`'s stdout opened `0102001` (`O_APPEND`) — its JSON events **append**
  directly to the `STDOUT` data file (8 event objects, 11.7 KB).
- Across a full STOP → START the file held **inode 34597343 unchanged and its
  12418 bytes / 8 events retained** — the restart did not reset it.
  *(Transparency: a second forced connection did not emit a fresh object inside the
  ~4 s sample window; retention-across-restart is what this trial proves, and it is
  unaffected by that.)*

---

## Verdict

**An IBM MQ `SERVER`-type service opens its `STDOUT` file `O_APPEND` and does not
truncate it on (re)start** (MQ 9.4.5, RHEL 9.6). Proven by the kernel open flags
(`0102001`) *and* by content persistence across start and restart at a stable
inode — two independent methods, agreeing.

The earlier "truncates on every (re)start" claim is **false**. Any design premised
on it — including using a launcher with `>>` *solely to avoid truncation* — rests
on a defect.

## Implications for the event-monitoring how-to

- **The launcher can be removed.** Point the service's `STDOUT` directly at the
  data file and run `amqsevt` as the `STARTCMD`; MQ appends. This deletes the
  launcher script, its install step, and the `.svc`/`.svc.err` split
  (`amqsevt`'s own stderr → `STDERR('<data-file>.err')` keeps diagnostics
  separate). This applies to the **file-sink** variant. The lab's production
  rollout — the shared `mq-event-monitor` role, now standard on every queue
  manager — deliberately **keeps** a launcher, *not* to avoid truncation but
  because it ships to **journald** rather than a file: it pipes
  `amqsevt -o json_compact` through `logger --size 32768` (tag `mq-events`),
  which a bare `STARTCMD` cannot do. The `O_APPEND` finding above stands either
  way.
- **What this does *not* fix — the real loss windows remain.** `amqsevt` consumes
  events **non-transactionally** (destructive get, then write), so a crash between
  the get and the write loses that event permanently. And on **HA failover** the
  data file is **host-local**: events written on the old node but not yet forwarded
  do not travel with the queue manager and are stranded. These are properties of
  `amqsevt`, independent of truncation; a lossless design needs a **transactional**
  consumer (get under syncpoint, commit only after the downstream forward is
  acknowledged), which `amqsevt` is not. The file how-to is a minimal-code,
  best-effort feed by choice, and must say so.

## Reproduction

Both scripts are committed on this branch and run as `mqm` on any RHEL MQ node:
`bash stdout-proof.sh` (Test 1) and `bash launcherless-proof.sh` (Test 2). Each
creates and deletes its own throwaway queue manager and leaves the host clean.
