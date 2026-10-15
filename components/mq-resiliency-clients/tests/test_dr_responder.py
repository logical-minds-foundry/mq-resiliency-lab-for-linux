"""mq-dr-responder copies the request CorrelId and dead-letters non-DR input (#1550).

Drives dr_responder.main() against tests/fake_dr_ibmmq.py, whose broker models MQ
message matching and syncpoint (puts visible at commit, gets restored on backout).
"""

from __future__ import annotations

import sys
import time

import pytest
from fake_dr_ibmmq import DEADQ, DLH_BYTES, Broker, MQMIError, message

from mqrc import deadletter, dr_mqi, dr_responder
from mqrc.dr.ledger import Event, Ledger
from mqrc.dr.wire import build_body
from mqrc.header import pack_header

IN, OUT = "SVC.REQUEST", "APP.REPLY"
RUN_ID = b"RUN-ID".ljust(24, b"\0")
APP_HEADER = pack_header(password="pw", sender="APP01", receiver="SVC", session_date="20260608")
MQFB_APPL_FIRST = 65536


_REAL_SLEEP = time.sleep


def _short_sleep(seconds: float) -> None:
    """Retry back-offs (0.2-0.5 s) cut short; still yields like the real one."""
    _REAL_SLEEP(min(seconds, 0.005))


def _request(seq: int, correl_id: bytes = RUN_ID):
    body = build_body(seq=seq, uuid=f"u{seq}", session_date="20260608", payload=f"MSG-{seq}")
    return message(APP_HEADER.encode() + body, correl_id=correl_id, msg_id=f"REQ{seq}".encode())


def _run(monkeypatch, tmp_path, broker: Broker, seconds: str = "0.15") -> int:
    monkeypatch.setitem(sys.modules, "ibmmq", broker.module())
    monkeypatch.setattr(
        dr_mqi,
        "connect_retry",
        lambda qm, conn, channel, keep_going, keyrepo="", certlabel="": broker.connect(keep_going),
    )
    argv = ["--qm", "SVCQM", "--ledger", str(tmp_path / "svc.jsonl"), "--seconds", seconds]
    return dr_responder.main(argv)


def test_reply_copies_the_request_correl_id_and_echoes_the_body(monkeypatch, tmp_path):
    other_run = b"OTHER-RUN".ljust(24, b"\0")
    broker = Broker({IN: [_request(1), _request(2, other_run)]})
    assert _run(monkeypatch, tmp_path, broker) == 0
    first, second = broker.queues[OUT]
    assert (first.CorrelId, second.CorrelId) == (RUN_ID, other_run)  # each run's own id
    assert first.body.endswith(b"DRv1|1|u1|20260608|MSG-1")
    assert first.body.startswith(b"pw      SVC     APP01   20260608")
    assert first.md["Persistence"] == 1
    assert len({id(md) for md in broker.put_mds}) == 2  # a fresh MD per put
    assert len({id(md) for md in broker.get_mds}) == len(broker.get_mds)  # and per get
    ledger = Ledger.read_jsonl(tmp_path / "svc.jsonl")
    assert ledger.svc_replied() == {1, 2}
    assert broker.queues[IN] == []


@pytest.mark.parametrize(
    "body",
    [
        b"REPLY:xxxxxxxx",  # no DRv1 marker at all (the find() == -1 case)
        APP_HEADER.encode() + b"DRv1|not-a-seq|u|20260608|p",  # marker, but malformed
    ],
)
def test_a_non_dr_message_is_dead_lettered_and_serving_continues(
    monkeypatch, tmp_path, capsys, body
):
    foreign = message(body, msg_id=b"FOREIGN-1".ljust(24, b"\0"), correl_id=b"C".ljust(24))
    broker = Broker({IN: [foreign, _request(3)]})
    assert _run(monkeypatch, tmp_path, broker) == 0
    [dead] = broker.queues[DEADQ]
    assert dead.body == DLH_BYTES + body  # MQDLH, then the original message, intact
    assert dead.MsgId == foreign.MsgId and dead.CorrelId == foreign.CorrelId  # traceable
    assert dead.md["Format"] == b"MQDEAD  "
    dl_pmo = dead.pmo
    assert dl_pmo.Options & 0x2  # MQPMO_SYNCPOINT: same unit of work as the get
    [reply] = broker.queues[OUT]  # the DR request behind it is still answered
    assert reply.body.endswith(b"DRv1|3|u3|20260608|MSG-3")
    assert broker.queues[IN] == []
    err = capsys.readouterr().err
    assert "dr-responder: dead-lettered message " + foreign.MsgId.hex() in err
    assert f"(MQDLH reason {MQFB_APPL_FIRST})" in err


def test_the_dead_letter_header_names_the_source(monkeypatch, tmp_path):
    broker = Broker({IN: [message(b"junk")]})
    seen = {}
    real_dlh = broker.module().DLH

    def dlh(**kw):
        seen.update(kw)
        return real_dlh(**kw)

    module = broker.module()
    module.DLH = dlh
    monkeypatch.setattr(broker, "module", lambda: module)
    assert _run(monkeypatch, tmp_path, broker) == 0
    assert seen["Reason"] == MQFB_APPL_FIRST
    assert (seen["DestQName"], seen["DestQMgrName"]) == (IN, "SVCQM")
    assert seen["PutApplName"] == "mq-dr-responder"


def test_a_failed_dead_letter_commit_is_retried(monkeypatch, tmp_path):
    broker = Broker({IN: [message(b"junk")]})
    broker.faults["commit:put"] = [2003, 2549]  # rolled back, then in doubt
    monkeypatch.setattr(dr_responder.time, "sleep", _short_sleep)
    assert _run(monkeypatch, tmp_path, broker) == 0
    assert len(broker.queues[DEADQ]) == 1  # moved exactly once
    assert broker.queues[IN] == []


def test_an_unexpected_dead_letter_error_backs_out_and_raises(monkeypatch, tmp_path):
    broker = Broker({IN: [message(b"junk")]})
    broker.faults[f"put:{DEADQ}"] = [2035]
    with pytest.raises(MQMIError):
        _run(monkeypatch, tmp_path, broker)
    assert broker.events[-2:] == ["backout", "disconnect"]
    assert [m.body for m in broker.queues[IN]] == [b"junk"]  # not consumed


def test_no_deadq_fails_loud_and_keeps_the_message(monkeypatch, tmp_path, capsys):
    broker = Broker({IN: [message(b"junk")]})
    broker.deadq = ""
    assert _run(monkeypatch, tmp_path, broker) == 1
    assert "dr-responder: SVCQM has no DEADQ" in capsys.readouterr().err
    assert broker.events[-2:] == ["backout", "disconnect"]  # backed out before the MQDISC
    assert [m.body for m in broker.queues[IN]] == [b"junk"]
    assert (tmp_path / "svc.jsonl").exists()  # the ledger is still written


def test_a_reply_error_backs_out_and_a_failed_backout_is_logged(monkeypatch, tmp_path, capsys):
    broker = Broker({IN: [_request(1)]})
    broker.faults[f"put:{OUT}"] = [2035]
    broker.faults["backout"] = [2009]
    with pytest.raises(MQMIError) as exc:
        _run(monkeypatch, tmp_path, broker)
    assert exc.value.reason == 2035  # the original error wins
    assert "dr-responder: backout failed" in capsys.readouterr().err
    assert broker.events[-2:] == ["backout", "disconnect"]


def test_rides_out_a_reconnect_and_in_place_retries(monkeypatch, tmp_path):
    broker = Broker({IN: [_request(1), _request(2)]})
    broker.faults = {
        f"get:{IN}": [2009, 2003],  # connection lost -> rebuild; then a clean rollback
        "disconnect": [2009],  # the dead connection's MQDISC fails; ignored
        "commit:put": [2161],  # quiescing: get+reply rolled back, re-got
    }
    monkeypatch.setattr(dr_responder.time, "sleep", _short_sleep)
    assert _run(monkeypatch, tmp_path, broker, seconds="0.3") == 0
    assert [m.CorrelId for m in broker.queues[OUT]] == [RUN_ID, RUN_ID]
    assert not any(broker.faults.values())
    ledger = Ledger.read_jsonl(tmp_path / "svc.jsonl")
    received = [e.seq for e in ledger.entries if e.event == Event.RECEIVED]
    assert received == [1, 2]  # recorded only after a clean commit


class _StepClock:
    """dr_responder's view of `time`: monotonic advances 1 s per read."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 1.0
        return self.now

    def sleep(self, _seconds: float) -> None:
        pass

    def time(self) -> float:
        return self.now


def test_flushes_the_ledger_while_it_serves(monkeypatch, tmp_path):
    broker = Broker({IN: [_request(1), _request(2), _request(3)]})
    flushes = []
    real_write = Ledger.write_jsonl
    monkeypatch.setattr(
        Ledger, "write_jsonl", lambda self, path: (flushes.append(path), real_write(self, path))
    )
    monkeypatch.setattr(dr_responder, "time", _StepClock())
    assert _run(monkeypatch, tmp_path, broker, seconds="30") == 0
    assert len(flushes) > 1  # mid-run flushes, plus the final write


def test_mqchar_strips_padding_from_bytes_and_str():
    assert deadletter.mqchar(b"Q1  \0\0") == "Q1"
    assert deadletter.mqchar("Q2   ") == "Q2"


def test_a_deadline_already_past_serves_nothing(monkeypatch, tmp_path):
    broker = Broker({IN: [_request(1)]})
    assert _run(monkeypatch, tmp_path, broker, seconds="0") == 0
    assert broker.events == []
    assert (tmp_path / "svc.jsonl").exists()


def test_an_unexpected_connect_error_raises_with_nothing_to_back_out(monkeypatch, tmp_path):
    broker = Broker()
    broker.faults["connect"] = [2035]
    with pytest.raises(MQMIError):
        _run(monkeypatch, tmp_path, broker)
    assert broker.events == []


def test_a_failed_final_disconnect_is_ignored(monkeypatch, tmp_path):
    broker = Broker({IN: [_request(1)]})
    broker.faults["disconnect"] = [2009]
    assert _run(monkeypatch, tmp_path, broker) == 0
    assert broker.faults["disconnect"] == []
