"""Unit tests for the app-requester round-trip signal (#429).

The pymqi MQ loop can't run here (no client libs), but the metric logic is pure —
app_requester imports pymqi lazily inside run(), so the module (and its
RoundTripStats / render_prom / write_textfile helpers) imports without it.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "app_requester", _REPO / "clients" / "app_requester.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ar = _load()


def test_stats_counts_successes_and_failures():
    s = ar.RoundTripStats()
    s.record_success(3.0)
    s.record_success(150.0)
    s.record_failure()
    assert (s.total, s.failures, s.successes) == (3, 1, 2)
    assert s.sum_ms == 153.0


def test_render_prom_histogram_is_cumulative_and_wellformed():
    s = ar.RoundTripStats()
    s.record_success(3.0)  # lands in le=5 and every larger bucket
    s.record_success(75.0)  # lands in le=100 and up
    s.record_failure()  # an attempt, not a latency observation
    text = ar.render_prom(s)
    assert "app_roundtrip_total 3" in text
    assert "app_roundtrip_failures_total 1" in text
    # cumulative buckets: 3ms only in le=5; both in le=100
    assert 'app_roundtrip_latency_ms_bucket{le="5"} 1' in text
    assert 'app_roundtrip_latency_ms_bucket{le="100"} 2' in text
    assert 'app_roundtrip_latency_ms_bucket{le="+Inf"} 2' in text
    assert "app_roundtrip_latency_ms_count 2" in text  # observations = successes
    assert "app_roundtrip_latency_ms_sum 78" in text
    # HELP/TYPE headers present (a valid exposition)
    assert "# TYPE app_roundtrip_latency_ms histogram" in text
    assert "# TYPE app_roundtrip_total counter" in text


def test_write_textfile_is_atomic_and_owner_only(tmp_path):
    p = tmp_path / "app_roundtrip.prom"
    ar.write_textfile(str(p), "hello\n")
    assert p.read_text() == "hello\n"
    # Owner-only mode bits (mkstemp's 0600) — no group/world read/write. node-exporter
    # reads it cross-user via a POSIX ACL set by provisioning, not a mode bit (#493).
    assert p.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [p]  # no leftover temp file


def test_publish_metrics_writes_the_textfile(tmp_path):
    s = ar.RoundTripStats()
    s.record_success(2.0)
    p = tmp_path / "app_roundtrip.prom"
    ar.publish_metrics(str(p), s)
    assert "app_roundtrip_total 1" in p.read_text()


def test_publish_metrics_survives_a_write_failure_but_shouts(monkeypatch, capsys):
    # A metrics-dir hiccup (e.g. the node-exporter textfile dir not yet writable)
    # must NEVER take down the round-trip workload — but it must be loud, not swallowed.
    s = ar.RoundTripStats()
    s.record_success(1.0)

    def _boom(_path, _text):
        raise PermissionError(13, "Permission denied", "/var/lib/node_exporter/textfile/x.tmp")

    monkeypatch.setattr(ar, "write_textfile", _boom)
    ar.publish_metrics("/var/lib/node_exporter/textfile/app_roundtrip.prom", s)  # must not raise
    out = capsys.readouterr().out
    assert "FAILED" in out and "Permission denied" in out  # loud, never swallowed


def test_default_args_stream_forever_at_one_per_second():
    args = ar._build_args([])
    assert args.count == 0  # 0 == infinite (the service default)
    assert args.rate == 1.0
    assert args.textfile == ""  # metrics off unless a path is given


# --- reconnect across a connection break (#457) ----------------------------


def test_is_reconnectable_distinguishes_connection_from_message_errors():
    assert ar._is_reconnectable(2009)  # MQRC_CONNECTION_BROKEN -> re-establish
    assert ar._is_reconnectable(2059)  # MQRC_Q_MGR_NOT_AVAILABLE -> re-establish
    # a missed reply (slow/absent) leaves the connection usable -> NOT a reconnect
    assert not ar._is_reconnectable(2033)  # MQRC_NO_MSG_AVAILABLE


def _fake_pymqi(state: dict, *, revive_on_connect: bool = True) -> types.ModuleType:
    """A minimal fake `pymqi` driving run()'s loop. The connection starts dead;
    connect_with_options() revives it (unless `revive_on_connect` is False — a QM
    that stays unreachable); the reply GET breaks the connection on the `break_at`-th
    get (modelling a Native HA failover) and it stays dead until the next connect.
    Lets us assert the requester re-establishes rather than looping a dead handle
    (#457)."""

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class CMQC:
        MQXPT_TCP = 1
        MQCNO_RECONNECT = 0x400000
        MQGMO_WAIT = 1
        MQGMO_FAIL_IF_QUIESCING = 0x2000
        MQMO_MATCH_CORREL_ID = 0x8
        MQFMT_STRING = b"MQSTR   "
        MQPER_PERSISTENT = 1

    class _Struct:  # stands in for CD / SCO / MD / GMO
        def __init__(self, **kw: object) -> None:
            self.MsgId = b""
            for key, value in kw.items():
                setattr(self, key, value)

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, _name, cd=None, sco=None, opts=0) -> None:
            state["connects"] += 1
            if revive_on_connect:
                state["alive"] = True

        def disconnect(self) -> None:
            pass

    class Queue:
        def __init__(self, _qmgr: object, _name: object) -> None:
            pass

        def put(self, _payload, md) -> None:
            if not state["alive"]:
                raise MQMIError(2, 2009)
            md.MsgId = b"MSGID"

        def get(self, _msg, _md, _gmo) -> bytes:
            if not state["alive"]:
                raise MQMIError(2, 2009)
            state["gets"] += 1
            if state["gets"] == state["break_at"]:
                state["alive"] = False  # the failover: connection dies mid-stream
                raise MQMIError(2, 2009)
            return b"REPLY:xxx"

    m = types.ModuleType("pymqi")
    for name, obj in {
        "MQMIError": MQMIError,
        "CMQC": CMQC,
        "CD": _Struct,
        "SCO": _Struct,
        "MD": _Struct,
        "GMO": _Struct,
        "QueueManager": QueueManager,
        "Queue": Queue,
    }.items():
        setattr(m, name, obj)
    return m


def test_requester_reconnects_after_a_connection_break(monkeypatch, capsys):
    # Break the connection on the 1st round-trip (a failover); the requester must
    # re-establish (a 2nd connect) and the remaining round-trips must succeed —
    # the old code looped the dead handle forever (one connect, all failures). (#457)
    state = {"alive": False, "connects": 0, "gets": 0, "break_at": 1}
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state))
    rc = ar.run(ar._build_args(["--count", "3", "--rate", "0"]))
    out = capsys.readouterr().out
    assert state["connects"] == 2  # initial + exactly one reconnect
    assert out.count("round-trip FAILED") == 1
    assert out.count("ms <- REPLY") == 2
    assert rc == 1  # nonzero exit because a failure was recorded


def test_requester_retries_when_the_reconnect_itself_fails(monkeypatch, capsys):
    # If the QM is still unreachable when we try to reconnect, the connect failure is
    # caught and retried next iteration (no crash, no dead-handle loop). Here the
    # connection never comes back, so every round-trip fails but the loop stays alive
    # and keeps attempting to reconnect. (#457)
    state = {"alive": False, "connects": 0, "gets": 0, "break_at": -1}
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state, revive_on_connect=False))
    rc = ar.run(ar._build_args(["--count", "3", "--rate", "0"]))
    out = capsys.readouterr().out
    assert state["connects"] == 3  # one reconnect attempt per iteration, no crash
    assert out.count("round-trip FAILED") == 3
    assert rc == 1
