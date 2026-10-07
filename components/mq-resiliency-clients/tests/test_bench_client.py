"""Unit tests for the purpose-built Native HA benchmark client (#1106, epic #227).

The pymqi PUT/commit loop can't run here (no client libs), but the client's
value-bearing logic is pure: the commit-latency percentile math, the
depth-trend flag, and the JSONL results record. bench_client imports pymqi
lazily inside run() and its reused app_requester helpers import lazily too, so
the module and its pure helpers import without the MQ client libs present.
"""

from __future__ import annotations

import json
import sys
import types

from mqrc import bench_client as bc

# --- percentile math -------------------------------------------------------


def test_percentiles_nearest_rank_on_1_to_100():
    # nearest-rank: rank = ceil(p/100 * n); value = sorted[rank-1]. For n=100
    # this makes each percentile land on its own integer, so the expected values
    # are exact and unambiguous.
    p = bc.percentiles(list(range(1, 101)), (50, 95, 99))
    assert p[50] == 50
    assert p[95] == 95
    assert p[99] == 99


def test_percentiles_is_order_independent():
    shuffled = [37, 1, 100, 50, 2, 99, 95, 96, 3, 51]
    assert bc.percentiles(shuffled, (50,)) == bc.percentiles(sorted(shuffled), (50,))


def test_percentiles_single_sample_is_that_sample():
    p = bc.percentiles([4.2], (50, 95, 99))
    assert p[50] == p[95] == p[99] == 4.2


def test_percentiles_empty_raises_not_silent():
    # No silent failures: an empty sample set is a real error, not a 0/NaN.
    try:
        bc.percentiles([], (50,))
    except ValueError:
        pass
    else:  # pragma: no cover - failure path only reached if the guard regresses
        raise AssertionError("percentiles([]) must raise ValueError, not return silently")


def test_percentiles_out_of_range_raises():
    try:
        bc.percentiles([1, 2, 3], (0,))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("percentile 0 must raise ValueError")


# --- latency summary (the record's latency block) --------------------------


def test_latency_summary_shape_and_values():
    s = bc.latency_summary(list(range(1, 101)))
    assert set(s) == {"p50", "p95", "p99", "max"}
    assert s["p50"] == 50
    assert s["p95"] == 95
    assert s["p99"] == 99
    assert s["max"] == 100  # max is the largest observed, not a percentile bucket


# --- depth-trend flag ------------------------------------------------------


def test_depth_bounded_when_flat():
    assert bc.depth_is_bounded([10, 11, 10, 12, 11, 10]) is True


def test_depth_unbounded_when_growing():
    # second half mean far exceeds the first half -> the QM can't keep up.
    assert bc.depth_is_bounded([1, 2, 3, 400, 800, 1200]) is False


def test_depth_bounded_trivially_for_short_series():
    assert bc.depth_is_bounded([]) is True
    assert bc.depth_is_bounded([5]) is True


# --- JSONL results record --------------------------------------------------


def test_results_record_shape():
    rec = bc.results_record(
        mode="strict",
        delay_ms=10,
        msg_size=2048,
        offered_rate=500.0,
        latencies=list(range(1, 101)),
        depths=[10, 10, 11, 10],
        elapsed_s=100.0,
        warmup_seconds=30,
        measure_seconds=120,
        timestamp="2026-09-15T00:00:00+00:00",
    )
    assert rec["mode"] == "strict"
    assert rec["delay_ms"] == 10
    assert rec["msg_size"] == 2048
    assert rec["offered_rate"] == 500.0
    assert rec["count"] == 100
    assert rec["achieved_rate"] == 1.0  # 100 commits / 100 s
    assert rec["depth_bounded"] is True
    assert rec["latency_ms"] == {"p50": 50, "p95": 95, "p99": 99, "max": 100}
    assert rec["warmup_seconds"] == 30
    assert rec["measure_seconds"] == 120
    assert rec["timestamp"] == "2026-09-15T00:00:00+00:00"


def test_results_record_default_timestamp_is_iso_utc():
    rec = bc.results_record(
        mode="async",
        delay_ms=0,
        msg_size=2048,
        offered_rate=100.0,
        latencies=[1.0, 2.0],
        depths=[1, 1],
        elapsed_s=2.0,
        warmup_seconds=1,
        measure_seconds=1,
    )
    # a real ISO-8601 UTC stamp, not empty/None
    assert rec["timestamp"].endswith("+00:00")


def test_append_jsonl_writes_one_line_per_record(tmp_path):
    path = tmp_path / "results.jsonl"
    bc.append_jsonl(str(path), {"mode": "async", "n": 1})
    bc.append_jsonl(str(path), {"mode": "strict", "n": 2})
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"mode": "async", "n": 1}
    assert json.loads(lines[1]) == {"mode": "strict", "n": 2}


def test_append_jsonl_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "dir" / "results.jsonl"
    bc.append_jsonl(str(path), {"ok": True})
    assert json.loads(path.read_text()) == {"ok": True}


# --- CLI argument surface --------------------------------------------------


def test_default_args_are_the_bench_contract():
    args = bc._build_args([])
    # persistent-message bench defaults per the epic's aligned numbers.
    assert args.msg_size == 2048
    assert args.warmup_seconds == 30
    assert args.measure_seconds == 120
    assert args.count == 0  # 0 = time-based measure window; >0 = fixed-N
    assert args.results == ""  # no artifact unless a path is given
    assert args.mode == "async"


def test_args_parse_the_sweep_point():
    args = bc._build_args(
        ["--qm", "NHARIAPP", "--mode", "strict", "--delay-ms", "20", "--offered-rate", "750"]
    )
    assert args.qm == "NHARIAPP"
    assert args.mode == "strict"
    assert args.delay_ms == 20
    assert args.offered_rate == 750.0


# --- reuse of app_requester's isolated pieces ------------------------------


def test_reuses_app_requester_helpers():
    # The client must reuse app_requester's stats/render/publish + reconnect
    # logic rather than re-implement them (#1106 / spec §3.5).
    assert bc.RoundTripStats is bc.app_requester.RoundTripStats
    assert bc.render_prom is bc.app_requester.render_prom
    assert bc.publish_metrics is bc.app_requester.publish_metrics
    assert bc._is_reconnectable is bc.app_requester._is_reconnectable


# --- no-commits diagnosis reports the real MQRC (#1380) ---------------------

_NAMES = {
    2009: "MQRC_CONNECTION_BROKEN",
    2035: "MQRC_NOT_AUTHORIZED",
    2085: "MQRC_UNKNOWN_OBJECT_NAME",
    2538: "MQRC_HOST_NOT_AVAILABLE",
}


def test_no_commits_message_2035_is_refused_not_unreachable():
    msg = bc.no_commits_message([2035] * 20, _NAMES)
    assert "unreachable" not in msg
    assert "refused every commit" in msg
    assert "most common MQRC 2035 MQRC_NOT_AUTHORIZED (x20 of 20)" in msg
    assert "last MQRC 2035 MQRC_NOT_AUTHORIZED" in msg


def test_no_commits_message_connection_failures_are_unreachable():
    msg = bc.no_commits_message([2538, 2538, 2009], _NAMES)
    assert "QM unreachable the whole window" in msg
    assert "2538 MQRC_HOST_NOT_AVAILABLE x2" in msg
    assert "2009 MQRC_CONNECTION_BROKEN x1" in msg


def test_no_commits_message_mixed_reports_most_common_and_last():
    # One connection blip does not make it "unreachable" when the QM refused the rest.
    msg = bc.no_commits_message([2009, 2035, 2035, 2085], _NAMES)
    assert "unreachable" not in msg
    assert "most common MQRC 2035 MQRC_NOT_AUTHORIZED (x2 of 4)" in msg
    assert "last MQRC 2085 MQRC_UNKNOWN_OBJECT_NAME" in msg


def test_no_commits_message_unknown_reason_name():
    msg = bc.no_commits_message([9999], {})
    assert "9999 MQRC_UNKNOWN" in msg


def test_no_commits_message_no_attempts():
    msg = bc.no_commits_message([], _NAMES)
    assert "no commit was attempted" in msg
    assert "unreachable" not in msg


def test_reason_names_reads_mqrc_constants_only():
    class CMQC:
        MQRC_NOT_AUTHORIZED = 2035
        MQRC_HOST_NOT_AVAILABLE = 2538
        MQXPT_TCP = 2  # not a reason code
        MQRC_NOT_AN_INT = "x"

    names = bc.reason_names(CMQC)
    assert names == {2035: "MQRC_NOT_AUTHORIZED", 2538: "MQRC_HOST_NOT_AVAILABLE"}


def _fake_pymqi(*, put_reason: int = 0, connect_reason: int = 0) -> types.ModuleType:
    """A fake pymqi for driving run(): every put (or every connect) fails with the
    given MQ reason, so the measure window produces no commits."""

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class CMQC:
        MQXPT_TCP = 2
        MQCNO_RECONNECT = 0x01000000
        MQFMT_STRING = b"MQSTR   "
        MQPER_PERSISTENT = 1
        MQPMO_SYNCPOINT = 0x02
        MQPMO_FAIL_IF_QUIESCING = 0x2000
        MQRC_CONNECTION_BROKEN = 2009
        MQRC_NOT_AUTHORIZED = 2035
        MQRC_HOST_NOT_AVAILABLE = 2538

    class _Struct:
        def __init__(self, **kw: object) -> None:
            for key, value in kw.items():
                setattr(self, key, value)

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, _name, cd=None, sco=None, opts=0) -> None:
            if connect_reason:
                raise MQMIError(2, connect_reason)

        def commit(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

    class Queue:
        def __init__(self, _qmgr: object, _name: object, open_opts: int = 0) -> None:
            pass

        def put(self, _msg, _md=None, _pmo=None) -> None:
            if put_reason:
                raise MQMIError(2, put_reason)

    m = types.ModuleType("pymqi")
    for name, obj in {
        "MQMIError": MQMIError,
        "CMQC": CMQC,
        "CD": _Struct,
        "SCO": _Struct,
        "MD": _Struct,
        "PMO": _Struct,
        "QueueManager": QueueManager,
        "Queue": Queue,
    }.items():
        setattr(m, name, obj)
    return m


_RUN_ARGS = ["--warmup-seconds", "0", "--count", "3", "--qm", "NHARCAPP"]


def test_run_put_2035_reports_not_authorized(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(put_reason=2035))
    assert bc.main(_RUN_ARGS) == 1
    out = capsys.readouterr().out
    assert "the QM refused every commit" in out
    assert "2035 MQRC_NOT_AUTHORIZED" in out
    assert "unreachable" not in out


def test_run_connect_failure_reports_unreachable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(connect_reason=2538))
    assert bc.main(_RUN_ARGS) == 1
    out = capsys.readouterr().out
    assert "QM unreachable the whole window" in out
    assert "2538 MQRC_HOST_NOT_AVAILABLE x3" in out
