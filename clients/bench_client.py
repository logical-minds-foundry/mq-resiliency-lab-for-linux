"""Purpose-built Native HA benchmark client (#1106, epic #227).

Measures the *sync tax* of persistent-message commits: the cost strict-sync
IRR (`SyncConsistency=Strict`) puts on the critical path of every commit vs
async CRR, as a function of injected cross-region latency. This is the
controlled instrument the epic's sweep (Task 8) drives -- deliberately NOT
`app_requester.py`, which self-describes as "good-enough noise, not a faithful
app model" and is the always-on cockpit stream, not a measurement tool.

What it does, per run (one {mode, delay, msg_size, offered_rate} sweep point):

  1. warmup   -- PUT+commit persistent messages under syncpoint at the offered
                 rate for `--warmup-seconds`, discarding the latencies (let the
                 QM, logs and replication reach steady state).
  2. measure  -- the same PUT+commit loop for `--measure-seconds` (or exactly
                 `--count` messages), timing each COMMIT (that is where the
                 replication round-trip lands) and sampling the queue depth.
  3. report   -- write one structured JSONL record (the primary output) with
                 the commit-latency percentiles (p50/p95/p99/max), the achieved
                 rate, and a depth-trend flag; and, like app_requester, keep
                 emitting the node-exporter textfile for live dashboards.

It REUSES the isolated, side-effect-clean pieces of app_requester -- the
`RoundTripStats`/`render_prom`/`write_textfile`/`publish_metrics` textfile
path and the `_RECONNECT_REASONS` Native-HA-failover reconnect logic -- rather
than re-implementing them (spec §3.5). The `import app_requester` is the same
in-directory reuse pattern dr_flow uses for dr_mqi: both scripts are deployed
side by side and run with the script dir on sys.path[0].

The pure logic (percentiles, depth-trend flag, JSONL record shape) is unit
tested in tests/test_bench_client.py; `clients/` is not under the `--cov=src`
gate, but the value-bearing math is covered there.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import UTC, datetime

import app_requester

# Reused app_requester pieces -- re-exported under this module so the reuse is
# explicit and unit-testable (test_reuses_app_requester_helpers).
RoundTripStats = app_requester.RoundTripStats
render_prom = app_requester.render_prom
write_textfile = app_requester.write_textfile
publish_metrics = app_requester.publish_metrics
_is_reconnectable = app_requester._is_reconnectable

# Default target: the CRR app QM. The sweep overrides --qm per mode
# (NHARCAPP for async CRR, NHARIAPP for strict IRR). Kept in sync with
# app_requester's env fallback.
DEFAULT_QM = os.environ.get("MQLAB_QM", "NHARCAPP")
DEFAULT_CONN = app_requester.DEFAULT_CONN


# --- pure logic (unit-tested) ----------------------------------------------


def percentiles(samples: list[float], pcts: tuple[float, ...]) -> dict[float, float]:
    """Nearest-rank percentiles of `samples` for each p in `pcts`.

    rank = ceil(p/100 * n); value = sorted(samples)[rank-1]. Nearest-rank (no
    interpolation) so two runs over the same data agree exactly and the values
    are unambiguous. Raises ValueError on an empty sample set or a p outside
    (0, 100] -- an empty bench window or a bad percentile is a real error, never
    a silently-returned 0/NaN (no silent failures)."""
    if not samples:
        raise ValueError("percentiles() requires at least one sample")
    ordered = sorted(samples)
    n = len(ordered)
    out: dict[float, float] = {}
    for p in pcts:
        if not 0 < p <= 100:
            raise ValueError(f"percentile {p} out of range (0, 100]")
        rank = math.ceil(p / 100 * n)
        out[p] = ordered[rank - 1]
    return out


def latency_summary(samples: list[float]) -> dict[str, float]:
    """The record's latency block: p50/p95/p99 (nearest-rank) + max (the largest
    observed commit latency, the tail the percentiles clip)."""
    pcts = percentiles(samples, (50, 95, 99))
    return {
        "p50": pcts[50],
        "p95": pcts[95],
        "p99": pcts[99],
        "max": max(samples),
    }


def depth_is_bounded(depths: list[float], *, tolerance: float = 0.10, abs_slack: float = 1.0) -> bool:
    """The depth-trend flag: True when queue depth shows no sustained upward
    trend over the measure window (the offered rate is sustainable).

    Compares the mean depth of the second half of the samples to the first
    half; bounded when the second half does not exceed the first by more than
    `tolerance` (fractional) plus `abs_slack` (absolute, to swallow small-count
    jitter). Fewer than two samples is trivially bounded (nothing suggests
    growth)."""
    if len(depths) < 2:
        return True
    mid = len(depths) // 2
    first = depths[:mid]
    second = depths[mid:]
    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    return second_mean <= first_mean * (1 + tolerance) + abs_slack


def results_record(
    *,
    mode: str,
    delay_ms: float,
    msg_size: int,
    offered_rate: float,
    latencies: list[float],
    depths: list[float],
    elapsed_s: float,
    warmup_seconds: float,
    measure_seconds: float,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Build the one-line JSONL results record for a sweep point.

    Primary output of a run: everything Task 8's comparison report needs to plot
    IRR-vs-CRR overhead vs injected latency -- the sweep coordinates
    (mode/delay/msg_size/offered_rate), the achieved throughput, the depth-trend
    flag, and the commit-latency percentiles."""
    achieved = len(latencies) / elapsed_s if elapsed_s > 0 else 0.0
    return {
        "mode": mode,
        "delay_ms": delay_ms,
        "msg_size": msg_size,
        "offered_rate": offered_rate,
        "achieved_rate": achieved,
        "count": len(latencies),
        "depth_bounded": depth_is_bounded(depths),
        "latency_ms": latency_summary(latencies),
        "warmup_seconds": warmup_seconds,
        "measure_seconds": measure_seconds,
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
    }


def append_jsonl(path: str, record: dict[str, object]) -> None:
    """Append one JSON record as a line to the results artifact, creating the
    parent directory if needed. Append (not replace) so a sweep accumulates all
    its points into one artifact."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


# --- CLI + measurement loop ------------------------------------------------


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Native HA persistent-commit benchmark client (#1106)")
    ap.add_argument("--qm", default=DEFAULT_QM, help="target queue manager (e.g. NHARCAPP | NHARIAPP)")
    ap.add_argument("--conn", default=DEFAULT_CONN, help="CONNAME list for the client connection")
    ap.add_argument("--channel", default="APP.SVRCONN")
    ap.add_argument("--queue", default="APP.REPLY", help="local queue to PUT persistent messages to")
    ap.add_argument("--msg-size", type=int, default=2048, help="persistent payload bytes (aligned: 2 KiB)")
    ap.add_argument("--offered-rate", type=float, default=0.0, help="offered messages/sec; 0 = as fast as commits allow")
    ap.add_argument("--warmup-seconds", type=float, default=30.0, help="warmup window (latencies discarded)")
    ap.add_argument("--measure-seconds", type=float, default=120.0, help="steady-state measure window")
    ap.add_argument("--count", type=int, default=0, help="fixed-N measured commits; 0 = use --measure-seconds")
    ap.add_argument("--results", default="", help="JSONL results artifact path; empty disables")
    ap.add_argument("--textfile", default="", help="node-exporter .prom path; empty disables live metrics")
    # Sweep-point labels recorded verbatim into the JSONL record (the netem
    # delay is injected host-side by `mqlab netem`, so the client is told it).
    ap.add_argument("--mode", default="async", choices=["async", "strict"], help="replication mode label")
    ap.add_argument("--delay-ms", type=float, default=0.0, help="injected one-way WAN delay (ms), for the record")
    # TLS (#250): --keyrepo is the keystore stem; a sibling .sth supplies the
    # password. Omit both -> plaintext.
    ap.add_argument("--keyrepo", default="", help="keystore stem, e.g. /home/vagrant/ssl/key")
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    return ap.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """The warmup + measure loop. Imports pymqi lazily so the module (and its
    pure helpers) imports without the MQ client libs present (unit tests)."""
    import pymqi

    cd = pymqi.CD(
        ChannelName=args.channel.encode(),
        ConnectionName=args.conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    sco = pymqi.SCO()
    if args.keyrepo:
        cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
        sco.KeyRepository = args.keyrepo.encode()
        if args.certlabel:
            sco.CertificateLabel = args.certlabel.encode()

    payload = b"x" * max(1, args.msg_size)
    interval = 1.0 / args.offered_rate if args.offered_rate > 0 else 0.0
    stats = RoundTripStats()

    def _connect() -> tuple:
        """(Re)establish the client connection + PUT handle. MQCNO_RECONNECT is
        kept; the outer loop also tears down and reconnects on a dead-connection
        reason (the Native HA failover crux, reused from app_requester #457)."""
        qmgr = pymqi.QueueManager(None)
        qmgr.connect_with_options(args.qm, cd=cd, sco=sco, opts=pymqi.CMQC.MQCNO_RECONNECT)
        return qmgr, pymqi.Queue(qmgr, args.queue)

    def _disconnect(qmgr: object) -> None:
        if qmgr is None:
            return
        try:
            qmgr.disconnect()
        except pymqi.MQMIError:
            pass

    state: dict[str, object] = {"qmgr": None, "queue": None}

    def _commit_once() -> float:
        """PUT one persistent message under syncpoint and COMMIT, returning the
        commit latency (ms) -- the sync-tax signal. On a dead-connection reason
        the connection is torn down so the caller reconnects next attempt."""
        if state["qmgr"] is None:
            state["qmgr"], state["queue"] = _connect()
        put_md = pymqi.MD(Format=pymqi.CMQC.MQFMT_STRING, Persistence=pymqi.CMQC.MQPER_PERSISTENT)
        pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT | pymqi.CMQC.MQPMO_FAIL_IF_QUIESCING)
        queue = state["queue"]
        qmgr = state["qmgr"]
        queue.put(payload, put_md, pmo)  # type: ignore[union-attr]
        t0 = time.monotonic()
        qmgr.commit()  # type: ignore[union-attr]
        return (time.monotonic() - t0) * 1000

    def _current_depth() -> float:
        qmgr = state["qmgr"]
        if qmgr is None:
            return 0.0
        q = pymqi.Queue(qmgr, args.queue, pymqi.CMQC.MQOO_INQUIRE)
        try:
            return float(q.inquire(pymqi.CMQC.MQIA_CURRENT_Q_DEPTH))
        finally:
            q.close()

    def _phase(duration_s: float, count: int, *, record: bool) -> tuple[list[float], list[float]]:
        """Run PUT+commit for `duration_s` seconds (or exactly `count` commits
        when count>0), pacing to the offered rate. Returns (latencies, depths);
        when record is False (warmup) both are discarded by the caller."""
        latencies: list[float] = []
        depths: list[float] = []
        start = time.monotonic()
        i = 0
        while (count > 0 and i < count) or (count == 0 and time.monotonic() - start < duration_s):
            try:
                dt = _commit_once()
                if record:
                    latencies.append(dt)
                    stats.record_success(dt)
                    if i % 100 == 0:
                        depths.append(_current_depth())
            except pymqi.MQMIError as exc:
                if record:
                    stats.record_failure()
                print(f"commit FAILED: {exc}", flush=True)
                if _is_reconnectable(exc.reason):
                    _disconnect(state["qmgr"])
                    state["qmgr"] = state["queue"] = None
            if record and args.textfile:
                publish_metrics(args.textfile, stats)
            i += 1
            if interval:
                time.sleep(interval)
        return latencies, depths

    try:
        _phase(args.warmup_seconds, 0, record=False)
        measure_start = time.monotonic()
        latencies, depths = _phase(args.measure_seconds, args.count, record=True)
        elapsed = time.monotonic() - measure_start
    finally:
        _disconnect(state["qmgr"])

    if not latencies:
        print("bench produced NO measured commits -- QM unreachable the whole window", flush=True)
        return 1

    record = results_record(
        mode=args.mode,
        delay_ms=args.delay_ms,
        msg_size=args.msg_size,
        offered_rate=args.offered_rate,
        latencies=latencies,
        depths=depths,
        elapsed_s=elapsed,
        warmup_seconds=args.warmup_seconds,
        measure_seconds=args.measure_seconds,
    )
    print(json.dumps(record), flush=True)
    if args.results:
        append_jsonl(args.results, record)
    return 0 if stats.failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    return run(_build_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
