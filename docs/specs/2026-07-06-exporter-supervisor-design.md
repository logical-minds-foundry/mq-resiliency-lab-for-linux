# The Watcher exporter supervisor — design

- **Task:** `mq-resiliency-lab-for-linux#503`, under epic `logical-minds-foundry/.github#21`
- **Status:** design (brainstorm output), approved
- **Date:** 2026-07-06

## Problem

mon-probe runs one client-mode `mq_prometheus` exporter per app queue manager
(`mq-exporter-<qm>.service`, `#172`). When a stack is **down** — never brought up
since the exporter launched — the exporter cannot `MQCONN` and **exits with code 10**;
`Restart=always`/`RestartSec=5` then restarts it every ~5s. On the live lab this was
observed at 900+ restarts for `pcmkapp` and `rdqmapp` (only the nha-ubuntu stack was up).

Consequences: journal spam, CPU churn, and — because the unit cycles through
`auto-restart`/`failed` rather than staying `active` — the Watcher's probe
"service" pill flaps down↔running.

### Grounded root cause (verified in the exporter source)

`mq_prometheus`'s `-ibmmq.keepRunning` is **already `true` by default**, but it only
guards *reconnection after a first successful connect*. `cmd/mq_prometheus/main.go`:

```go
if !isConnectedOnce() || !config.keepRunning {
    // If we've never successfully connected, then exit instead
    // of retrying as it probably means a config error
    setCollectorEnd(true)   // → process exits (the exit-10 crash-loop)
}
```

So an exporter that has connected once survives any later outage (failover, DR,
teardown) and reconnects on its own. The crash-loop is **exclusively the cold-start
case**: a QM that was never up when the exporter launched. This is deliberate,
hardcoded behavior — **no flag changes it**.

## Principle

A monitoring component must not crash-loop because the thing it monitors is
unavailable. Its job is to stay up and *report* the target's state — including
"down." The fix keeps the exporter service alive across a target that is (or
becomes) reachable, without masking a genuine misconfiguration.

## Design

A small **stdlib-only Python supervisor**, deployed to mon-probe by the
`mq-exporter` role. The per-stack systemd unit's `ExecStart` calls the supervisor
instead of `mq_prometheus` directly; the supervisor launches the **exact same**
exporter command (same flags, TLS, CCDT). Because the supervisor process runs
continuously — waiting and retrying — **the systemd unit stays `active`**, which is
what makes the probe pill stop flapping.

No separate liveness probe: the exporter is its own check (a bare port check can
pass before the QM/TLS is truly ready). Division of labor: **the supervisor owns
cold-start; the exporter's built-in `keepRunning` owns steady-state.**

### Supervise loop

Run the exporter as a child, **streaming its output to the journal** (glass-box).
On child exit:

- **Connected-once** (child ran past a short threshold, i.e. it got in and later
  died) → reset backoff, retry promptly. Rare in steady state (`keepRunning` holds
  it through outages); a fast reconnect is correct if it ever crashes.
- **Never-connected** (died fast) → the cold-start case. Extract the MQ reason code
  from the output and **log it loudly, classified**: `2059`/`2538` (queue manager /
  host not available) = *expected, the stack is down*; any other RC = *possible
  misconfiguration — investigate*. Then sleep (backoff) and retry.

### Backoff and the no-silent-failure guarantee

Capped exponential: **start 30s, double each failure, cap 5 min, reset to 30s on a
successful connect**, retry forever. Retrying forever is correct for the lab (a
stack can be down indefinitely), and nothing is masked — every attempt prints the
classified RC, so a genuine config error is plainly visible in the journal *and* in
the board's down "exporters N/M" tile; it simply does not crash-loop while visible.

### Auto-wake

Bring a down stack up later and the supervisor's next retry connects — the monitor
"wakes up" with no manual step. This is a deliberately lab-shaped convenience; the
mon-probe exporters are the support layer's per-stack coupling point.

## Components & boundaries

- **`exporter_supervisor` (new, Python, stdlib-only):** owns the supervise loop.
  Pure, independently testable units — the backoff schedule, the RC classification
  (down vs. misconfig), and the output→RC extraction are pure functions; the loop
  drives a child process and is tested against a fake child. Deployed to mon-probe
  and invoked by the systemd unit via an absolute path.
- **`mq-exporter` role (modified):** deploys the supervisor script and rewrites the
  unit template's `ExecStart` to wrap the exporter command. Applies uniformly to
  every per-stack instance. No change to the exporter flags, TLS, or CCDT.

## Testing

- Unit tests to the repo's 100%-branch gate: backoff schedule, RC classification,
  output→RC extraction (pure functions); the supervise loop against a fake child
  (exits-fast / runs-then-dies / connects-then-held). No live QM needed.
- `ansible-lint` covers the role/unit change.
- Live proof at the cold-boot/observe pass: with pcmk/rdqm/nha-rhel stacks down,
  their `mq-exporter-*` units stay `active` (no restart churn), the journal shows
  classified back-off retries, and the Watcher probe pill reads a stable "running";
  bringing a down stack up lights its exporter without intervention.

## Out of scope

- The **app-client requester** may have a similar "crashes when its target app-QM is
  down" coupling — worth checking separately, not widened into this task.
- Production exporter-lifecycle management (tying exporter start to QM lifecycle) is a
  real-world concern the lab deliberately trades for the auto-wake convenience.
