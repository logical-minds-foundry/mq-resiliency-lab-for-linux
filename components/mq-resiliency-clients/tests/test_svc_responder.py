"""Unit tests for the SVC service responder (#147; first tests, epic .github#294 T8).

The real MQ loop can't run here (no client libs). svc_responder imports ibmmq lazily
inside serve()/main(), so the module imports without it and we drive main() with a
fake ibmmq -- mirroring tests/test_app_requester.py. The fake records every reply put
(its object descriptor, MQMD and body) plus commits and disconnects, so the
request/reply correlation contract is asserted without a live queue manager.
"""

from __future__ import annotations

import sys
import time
import types

from mqrc import svc_responder

NO_MSG = 2033  # MQRC_NO_MSG_AVAILABLE
BROKEN = 2009  # MQRC_CONNECTION_BROKEN
MISSING_REPLY_TO_Q = 2027  # MQRC_MISSING_REPLY_TO_Q
DLH_BYTES = b"<MQDLH>"
BLANK_Q = b" " * 48  # a blank MQCHAR48, as the real GET fills it


def _fake_ibmmq(state: dict) -> types.ModuleType:
    """A minimal fake `ibmmq` for serve(). `state["requests"]` is the queue of
    (MsgId, ReplyToQ, ReplyToQMgr, body) requests on the in-queue; once drained a GET
    reports MQRC_NO_MSG_AVAILABLE. `state["raise_at_get"]` / `state["raise_at_connect"]` /
    `state["raise_at_put"]` / `state["raise_at_backout"]` hold a reason code to raise
    there instead. `state["deadq"]` is the QM's DEADQ (default SYSTEM.DEAD.LETTER.QUEUE,
    blank-padded like the real inquire); `state["events"]` records commit / backout /
    disconnect in order."""

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class CMQXC:
        MQXPT_TCP = 2

    class CMQC:
        MQCNO_CLIENT_BINDING = 0x800
        MQGMO_SYNCPOINT = 0x2
        MQGMO_WAIT = 0x1
        MQGMO_FAIL_IF_QUIESCING = 0x2000
        MQPMO_SYNCPOINT = 0x2
        MQPMO_NEW_MSG_ID = 0x40
        MQPMO_FAIL_IF_QUIESCING = 0x2000
        MQPER_PERSISTENT = 1
        MQOO_OUTPUT = 0x10
        MQOO_FAIL_IF_QUIESCING = 0x2000
        MQRC_NO_MSG_AVAILABLE = NO_MSG
        MQRC_MISSING_REPLY_TO_Q = MISSING_REPLY_TO_Q
        MQCA_DEAD_LETTER_Q_NAME = 2006
        MQFMT_DEAD_LETTER_HEADER = b"MQDEAD  "
        MQAT_UNIX = 6

    class _Struct:  # stands in for CD / SCO / MD / OD / GMO / PMO
        def __init__(self, **kw: object) -> None:
            self.MsgId = b""
            self.ReplyToQ = b""
            self.ReplyToQMgr = b""
            self.Format = b""
            self.CorrelId = b""
            self.MsgType = 8
            self.Persistence = 1
            self.Priority = 0
            self.Encoding = 546
            self.CodedCharSetId = 1208
            for key, value in kw.items():
                setattr(self, key, value)

    class SCO(_Struct):
        def __init__(self, **kw: object) -> None:
            super().__init__(**kw)
            state["sco"] = self

    class DLH(_Struct):
        def __init__(self, **kw: object) -> None:
            super().__init__(**kw)
            state["dlh"] = self

        def pack(self) -> bytes:
            return DLH_BYTES

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, name: str, cd: _Struct, sco: object, opts: int = 0) -> None:
            state["connect"] = (name, cd, sco)
            state["opts"] = opts
            if "raise_at_connect" in state:
                raise MQMIError(2, state["raise_at_connect"])

        def commit(self) -> None:
            state["commits"] = state.get("commits", 0) + 1
            state.setdefault("events", []).append("commit")

        def backout(self) -> None:
            state.setdefault("events", []).append("backout")
            if "raise_at_backout" in state:
                raise MQMIError(2, state["raise_at_backout"])

        def inquire(self, selector: int) -> bytes:
            assert selector == CMQC.MQCA_DEAD_LETTER_Q_NAME
            return state.get("deadq", b"SYSTEM.DEAD.LETTER.QUEUE").ljust(48)

        def disconnect(self) -> None:
            state["disconnected"] = True
            state.setdefault("events", []).append("disconnect")

    class Queue:
        def __init__(self, _qmgr: object, name: str | None = None) -> None:
            self.name = name
            self.od: _Struct | None = None

        def get(self, _buf: object, md: _Struct, _gmo: object) -> bytes:
            if "raise_at_get" in state:
                raise MQMIError(2, state["raise_at_get"])
            if not state["requests"]:
                time.sleep(0.005)  # the real GET waits; don't busy-spin the test
                raise MQMIError(2, NO_MSG)
            msg_id, reply_q, reply_qm, body = state["requests"].pop(0)
            md.MsgId, md.ReplyToQ, md.ReplyToQMgr, md.Format = (
                msg_id,
                reply_q,
                reply_qm,
                b"MQSTR   ",
            )
            return body

        def open(self, od: _Struct, opts: int) -> None:
            self.od = od
            state["open_opts"] = opts

        def put(self, body: bytes, md: _Struct, pmo: _Struct) -> None:
            if "raise_at_put" in state:
                raise MQMIError(2, state["raise_at_put"])
            state.setdefault("replies", []).append((self.od, md, body, pmo))

        def close(self) -> None:
            pass

    mod = types.ModuleType("ibmmq")
    mod.MQMIError = MQMIError
    mod.CMQC = CMQC
    mod.CMQXC = CMQXC
    mod.CD = mod.MD = mod.OD = mod.GMO = mod.PMO = _Struct
    mod.SCO = SCO
    mod.DLH = DLH
    mod.QueueManager = QueueManager
    mod.Queue = Queue
    return mod


def _run(monkeypatch, state: dict, *extra: str) -> int:
    monkeypatch.setitem(sys.modules, "ibmmq", _fake_ibmmq(state))
    argv = ["--qm", "SVCQM", "--in-queue", "SVC.REQUEST", "--seconds", "0.05", *extra]
    return svc_responder.main(argv)


def test_replies_to_the_reply_to_queue_with_the_correlation_id(monkeypatch):
    state: dict = {"requests": [(b"MSGID-1", b"APP.REPLY", b"PCMKAPP", b"hello")]}
    assert _run(monkeypatch, state) == 0
    [(od, md, body, pmo)] = state["replies"]
    assert (od.ObjectName, od.ObjectQMgrName) == (b"APP.REPLY", b"PCMKAPP")
    assert md.CorrelId == b"MSGID-1"  # reply CorrelId = request MsgId
    assert md.Format == b"MQSTR   "
    assert body == b"REPLY:hello"
    assert pmo.Options & 0x40  # MQPMO_NEW_MSG_ID: the reply gets its own MsgId
    assert state["commits"] == 1  # get + put under one syncpoint commit
    assert state["disconnected"]


def test_seconds_expiry_exits_zero_with_no_traffic(monkeypatch):
    state: dict = {"requests": []}
    assert _run(monkeypatch, state) == 0
    assert "replies" not in state
    assert state["disconnected"]
    name, cd, sco = state["connect"]
    assert state["opts"] == 0x800  # MQCNO_CLIENT_BINDING forced (#1480)
    assert (name, cd.ChannelName, cd.ConnectionName) == (
        "SVCQM",
        "SVC.SVRCONN",
        "localhost(1414)",
    )
    assert sco is None  # no --keyrepo -> plaintext connect


def test_mq_error_is_logged_and_exits_nonzero(monkeypatch, capsys):
    state: dict = {"requests": [], "raise_at_get": BROKEN}
    assert _run(monkeypatch, state) == 1
    assert "reason 2009" in capsys.readouterr().err
    assert state["events"] == ["backout", "disconnect"]


def test_connect_error_is_logged_and_exits_nonzero(monkeypatch, capsys):
    state: dict = {"requests": [], "raise_at_connect": 2059}
    assert _run(monkeypatch, state) == 1
    assert "reason 2059" in capsys.readouterr().err


def test_tls_sets_cipher_keyrepo_and_cert_label(monkeypatch):
    state: dict = {"requests": []}
    assert _run(monkeypatch, state, "--keyrepo", "/var/mqm/ssl/k", "--certlabel", "svc") == 0
    _name, cd, sco = state["connect"]
    assert cd.SSLCipherSpec == "ANY_TLS13_OR_HIGHER"
    assert sco.KeyRepository == "/var/mqm/ssl/k"
    assert sco.CertificateLabel == "svc"


def test_tls_without_cert_label_leaves_the_default(monkeypatch):
    state: dict = {"requests": []}
    assert _run(monkeypatch, state, "--keyrepo", "/var/mqm/ssl/k") == 0
    _name, _cd, sco = state["connect"]
    assert sco.KeyRepository == "/var/mqm/ssl/k"
    assert not hasattr(sco, "CertificateLabel")


# --- unanswerable messages + backout on error (#1513) ---------------------------------


def test_blank_reply_to_is_dead_lettered_and_serving_continues(monkeypatch, capsys):
    state: dict = {
        "requests": [
            (b"DGRAM-1", BLANK_Q, b"NHAUAPP", b"DRv1|45|probe"),  # a mq-dr-flow datagram
            (b"MSGID-2", b"APP.REPLY", b"NHAUAPP", b"hello"),
        ]
    }
    assert _run(monkeypatch, state) == 0
    [(dl_od, dl_md, dl_body, dl_pmo), (od, _md, body, _pmo)] = state["replies"]
    assert dl_od.ObjectName == "SYSTEM.DEAD.LETTER.QUEUE"  # the inquired DEADQ, unpadded
    assert dl_body == DLH_BYTES + b"DRv1|45|probe"  # MQDLH, then the original message
    assert dl_md.Format == b"MQDEAD  "
    assert dl_md.MsgId == b"DGRAM-1"  # traceable back to the original
    assert dl_pmo.Options & 0x2  # MQPMO_SYNCPOINT: in the same unit of work as the get
    assert not dl_pmo.Options & 0x40  # no MQPMO_NEW_MSG_ID
    dlh = state["dlh"]
    assert dlh.Reason == MISSING_REPLY_TO_Q
    assert (dlh.DestQName, dlh.DestQMgrName) == ("SVC.REQUEST", "SVCQM")
    assert (dlh.Format, dlh.Encoding, dlh.CodedCharSetId) == (b"MQSTR   ", 546, 1208)
    assert len(dlh.PutDate) == 8 and len(dlh.PutTime) == 8
    # the next, answerable request is still served
    assert (od.ObjectName, body) == (b"APP.REPLY", b"REPLY:hello")
    assert state["events"] == ["commit", "commit", "disconnect"]
    err = capsys.readouterr().err
    assert "dead-lettered message " + b"DGRAM-1".hex() in err
    assert "MQRC 2027" in err


def test_no_deadq_fails_loud_and_backs_out(monkeypatch, capsys):
    state: dict = {
        "requests": [(b"DGRAM-1", BLANK_Q, b"NHAUAPP", b"probe")],
        "deadq": b"",
    }
    assert _run(monkeypatch, state) == 1
    assert "SVCQM has no DEADQ" in capsys.readouterr().err
    assert "replies" not in state
    assert state["events"] == ["backout", "disconnect"]  # the get is not consumed


def test_reply_error_backs_out_before_the_disconnect(monkeypatch, capsys):
    state: dict = {
        "requests": [(b"MSGID-1", b"APP.REPLY", b"NHAUAPP", b"hello")],
        "raise_at_put": 2184,
    }
    assert _run(monkeypatch, state) == 1
    assert "reason 2184" in capsys.readouterr().err
    # a normal MQDISC commits; backing out first keeps the request on the queue
    assert state["events"] == ["backout", "disconnect"]


def test_backout_failure_is_logged_and_the_original_error_wins(monkeypatch, capsys):
    state: dict = {
        "requests": [(b"MSGID-1", b"APP.REPLY", b"NHAUAPP", b"hello")],
        "raise_at_put": 2184,
        "raise_at_backout": BROKEN,
    }
    assert _run(monkeypatch, state) == 1
    err = capsys.readouterr().err
    assert "backout failed" in err and "reason 2009" in err
    assert "MQ error" in err and "reason 2184" in err
    assert state["events"] == ["backout", "disconnect"]
