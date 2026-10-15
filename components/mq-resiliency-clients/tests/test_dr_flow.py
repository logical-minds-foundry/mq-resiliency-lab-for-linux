"""mq-dr-flow takes only its own replies and fails loud (#1550).

Drives dr_flow.main() end to end against tests/fake_dr_ibmmq.py, whose broker models MQ
message matching and syncpoint. A stand-in responder (Broker.on_commit_put) answers each
committed request on APP.REPLY the way mq-dr-responder does: it echoes the DRv1 body and
copies the request's CorrelId onto the reply.
"""

from __future__ import annotations

import re
import sys
import threading
import time

import pytest
from fake_dr_ibmmq import NONE24, Broker, message

from mqrc import dr_flow, dr_mqi
from mqrc.dr.ledger import Ledger
from mqrc.dr.wire import NotDRv1Error, build_body
from mqrc.header import pack_header

REQ, REPLY = "SVC.REQUEST", "APP.REPLY"
FOREIGN_CORREL = b"APP-REQUESTER".ljust(24, b"\0")
HEADER = pack_header(password="pw", sender="SVC", receiver="APP01", session_date="20260608")


_REAL_SLEEP = time.sleep


def _short_sleep(seconds: float) -> None:
    """Retry back-offs (0.2-0.5 s) cut short; still yields like the real one."""
    _REAL_SLEEP(min(seconds, 0.005))


def _echo(broker: Broker, *, reply_body=None):
    """Answer each committed request on REPLY, copying its CorrelId (mq-dr-responder)."""

    def on_commit_put(queue, msg):
        if queue != REQ:
            return
        body = msg.body[msg.body.find(b"DRv1|") :]
        out = reply_body if reply_body is not None else HEADER.encode() + body
        with broker.lock:
            broker.queues.setdefault(REPLY, []).append(message(out, correl_id=msg.CorrelId))

    broker.on_commit_put = on_commit_put


def _install(monkeypatch, broker: Broker) -> None:
    monkeypatch.setitem(sys.modules, "ibmmq", broker.module())
    monkeypatch.setattr(
        dr_mqi,
        "connect_retry",
        lambda qm, conn, channel, keep_going, keyrepo="", certlabel="": broker.connect(keep_going),
    )
    monkeypatch.setattr(dr_flow, "DRAIN_SECONDS", 0.3)


def _run(monkeypatch, tmp_path, broker: Broker, *extra: str) -> int:
    _install(monkeypatch, broker)
    argv = ["--ledger", str(tmp_path / "app.jsonl"), "--rate", "200", "--seconds", "0.15"]
    try:
        return dr_flow.main([*argv, *extra])
    finally:
        dr_flow.STOP.clear()  # module-global: don't leak a set STOP into other tests


def _counts(out: str) -> tuple[int, int]:
    match = re.search(r"flow done: (\d+) sent, (\d+) confirmed", out)
    assert match, out
    return int(match.group(1)), int(match.group(2))


def test_run_correl_id_is_24_bytes_and_unique_per_run():
    first, second = dr_flow.new_run_correl_id(), dr_flow.new_run_correl_id()
    assert len(first) == len(second) == 24
    assert first != second
    assert first != NONE24


def test_parse_reply_rejects_a_message_without_a_drv1_body():
    # the live #1509 failure: find() == -1 sliced the last byte (b'x') and parsed that
    with pytest.raises(NotDRv1Error, match="no DRv1 body"):
        dr_flow._parse_reply(b"REPLY:xxxxxxxx")
    body = build_body(seq=7, uuid="u7", session_date="20260608", payload="MSG-7")
    assert dr_flow._parse_reply(HEADER.encode() + body).seq == 7


def test_consumes_only_its_own_replies_and_leaves_foreign_ones(monkeypatch, tmp_path, capsys):
    foreign = [
        message(b"REPLY:xxxxxxxx", correl_id=FOREIGN_CORREL),  # an app-requester reply
        message(b"REPLY:yyyyyyyy"),  # even an uncorrelated one is not taken
    ]
    broker = Broker({REPLY: list(foreign)})
    _echo(broker)
    assert _run(monkeypatch, tmp_path, broker) == 0
    sent, confirmed = _counts(capsys.readouterr().out)
    assert sent > 0
    assert confirmed == sent  # every request's reply found by correlation
    assert broker.queues[REPLY] == foreign  # never consumed, never mis-parsed

    request_mds = broker.put_mds
    run_id = request_mds[0].CorrelId
    assert len(run_id) == 24 and run_id != NONE24
    assert all(md.CorrelId == run_id for md in request_mds)  # one run id on every request
    assert len({id(md) for md in request_mds}) == len(request_mds)  # a fresh MD per put
    get_mds = broker.get_mds
    assert len({id(md) for md in get_mds}) == len(get_mds)  # a fresh MD per get
    assert all(md.CorrelId == run_id for md in get_mds)

    ledger = Ledger.read_jsonl(tmp_path / "app.jsonl")
    assert len(ledger.confirmed_seqs()) == confirmed


def test_a_second_run_does_not_take_the_first_runs_replies(monkeypatch, tmp_path, capsys):
    # a reply left behind by an earlier mq-dr-flow run carries THAT run's id
    leftover = message(
        HEADER.encode() + build_body(seq=1, uuid="old", session_date="20260608", payload="x"),
        correl_id=dr_flow.new_run_correl_id(),
    )
    broker = Broker({REPLY: [leftover]})
    _echo(broker)
    assert _run(monkeypatch, tmp_path, broker) == 0
    sent, confirmed = _counts(capsys.readouterr().out)
    assert confirmed == sent
    assert broker.queues[REPLY] == [leftover]


def test_a_correlated_reply_without_a_drv1_body_fails_loud(monkeypatch, tmp_path, capsys):
    broker = Broker()
    _echo(broker, reply_body=b"garbage")
    started = time.monotonic()
    # --seconds 30: the consumer's failure must STOP the producer, not wait it out
    assert _run(monkeypatch, tmp_path, broker, "--seconds", "30") == 1
    assert time.monotonic() - started < 10
    captured = capsys.readouterr()
    assert "mq-dr-flow: consumer failed" in captured.err
    assert "NotDRv1Error" in captured.err and "FAILED -- consumer" in captured.err
    assert "flow done:" in captured.out  # the ledger is still written and summarised
    assert "backout" in broker.events  # the faulty reply is left on the queue, not consumed
    assert any(m.body == b"garbage" for m in broker.queues[REPLY])


@pytest.mark.parametrize(
    ("fault", "side"),
    [
        ("put:SVC.REQUEST", "producer"),  # an unexpected put error
        ("commit:put", "producer"),  # an unexpected commit error
        ("get:APP.REPLY", "consumer"),  # an unexpected get error
        ("commit:get", "consumer"),  # an unexpected commit error
    ],
)
def test_an_unexpected_mq_error_in_either_thread_fails_loud(
    monkeypatch, tmp_path, capsys, fault, side
):
    broker = Broker()
    _echo(broker)
    broker.faults[fault] = [2035]  # MQRC_NOT_AUTHORIZED: in no retry family
    assert _run(monkeypatch, tmp_path, broker, "--seconds", "30") == 1
    err = capsys.readouterr().err
    assert f"mq-dr-flow: {side} failed" in err
    assert f"FAILED -- {side}" in err and "Reason 2035" in err


def test_producer_and_consumer_ride_out_reconnects_and_retries(monkeypatch, tmp_path, capsys):
    broker = Broker()
    _echo(broker)
    broker.faults = {
        "put:SVC.REQUEST": [2009, 2549, 2003],  # reconnect, in-doubt, clean rollback
        "commit:put": [2161],
        "get:APP.REPLY": [2009, 2003, 2549],
        "commit:get": [2009, 2003],
        "disconnect": [2009, 2009],  # a dead connection's MQDISC fails; ignored
        "backout": [2009],  # a failed backout of a rolled-back UOW is ignored
    }
    monkeypatch.setattr(dr_flow.time, "sleep", _short_sleep)
    assert _run(monkeypatch, tmp_path, broker, "--seconds", "0.3") == 0
    sent, confirmed = _counts(capsys.readouterr().out)
    assert sent > 0 and confirmed == sent  # every seq resent and confirmed once
    assert not any(broker.faults.values())  # every injected fault was hit


class _NoConnection:
    def connect_retry(self, _keep_going):
        return None


def test_both_sides_stop_when_no_connection_can_be_made(monkeypatch, tmp_path):
    broker = Broker()
    monkeypatch.setitem(sys.modules, "ibmmq", broker.module())
    ledger, lock = Ledger(), threading.Lock()
    path = tmp_path / "app.jsonl"
    dr_flow.producer(_NoConnection(), 10, 5, None, REQ, NONE24, ledger, lock, path)
    dr_flow.consumer(_NoConnection(), REPLY, NONE24, ledger, lock)
    assert broker.events == []


class _StepClock:
    """dr_flow's view of `time`: monotonic advances 1 s per read, sleep returns at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now

    def sleep(self, _seconds: float) -> None:
        pass

    def time(self) -> float:
        return self.now


def test_producer_flushes_the_ledger_while_it_runs(monkeypatch, tmp_path):
    broker = Broker()
    monkeypatch.setitem(sys.modules, "ibmmq", broker.module())
    monkeypatch.setattr(dr_flow, "time", _StepClock())

    class _Conn:
        def connect_retry(self, keep_going):
            return broker.connect(keep_going)

    broker.faults["disconnect"] = [2009]  # a failed final MQDISC is ignored
    ledger, lock = Ledger(), threading.Lock()
    path = tmp_path / "app.jsonl"
    dr_flow.producer(_Conn(), 10, 12, 5.0, REQ, NONE24, ledger, lock, path)
    assert path.exists()  # flushed mid-run, not only at exit
    assert broker.put_mds[0].Expiry == 50  # --expiry seconds -> tenths
    assert broker.events[-1] == "disconnect"
    assert broker.faults["disconnect"] == []


def test_consumer_rebuilds_after_a_broken_commit_and_ignores_failed_disconnects(monkeypatch):
    run_id = dr_flow.new_run_correl_id()
    body = build_body(seq=4, uuid="u4", session_date="20260608", payload="MSG-4")
    broker = Broker({REPLY: [message(HEADER.encode() + body, correl_id=run_id)]})
    broker.faults = {"commit:get": [2009], "disconnect": [2009, 2009]}
    monkeypatch.setitem(sys.modules, "ibmmq", broker.module())
    monkeypatch.setattr(dr_flow.time, "sleep", _short_sleep)

    class _Conn:
        connects = 0

        def connect_retry(self, keep_going):
            self.connects += 1
            if self.connects == 2:
                dr_flow.STOP.set()  # the rebuilt connection is the last one
            return broker.connect(lambda: True)

    ledger, lock = Ledger(), threading.Lock()
    try:
        dr_flow.consumer(_Conn(), REPLY, run_id, ledger, lock)
    finally:
        dr_flow.STOP.clear()
    assert ledger.confirmed_seqs() == {4}  # re-got on the rebuilt connection, once
    assert broker.faults == {"commit:get": [], "disconnect": []}
    assert broker.events.count("disconnect") == 2
