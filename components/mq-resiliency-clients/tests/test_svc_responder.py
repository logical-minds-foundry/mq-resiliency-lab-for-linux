"""Unit tests for the SVC service responder (#147; first tests, epic .github#294 T8).

The real MQ loop can't run here (no client libs). svc_responder imports pymqi lazily
inside serve()/main(), so the module imports without it and we drive main() with a
fake pymqi -- mirroring tests/test_app_requester.py. The fake records every reply put
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


def _fake_pymqi(state: dict) -> types.ModuleType:
    """A minimal fake `pymqi` for serve(). `state["requests"]` is the queue of
    (MsgId, ReplyToQ, ReplyToQMgr, body) requests on the in-queue; once drained a GET
    reports MQRC_NO_MSG_AVAILABLE. `state["raise_at_get"]` / `state["raise_at_connect"]`
    hold a reason code to raise there instead."""

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class CMQC:
        MQXPT_TCP = 1
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

    class _Struct:  # stands in for CD / SCO / MD / OD / GMO / PMO
        def __init__(self, **kw: object) -> None:
            self.MsgId = b""
            self.ReplyToQ = b""
            self.ReplyToQMgr = b""
            self.Format = b""
            for key, value in kw.items():
                setattr(self, key, value)

    class SCO(_Struct):
        def __init__(self, **kw: object) -> None:
            super().__init__(**kw)
            state["sco"] = self

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, name: str, cd: _Struct, sco: object) -> None:
            state["connect"] = (name, cd, sco)
            if "raise_at_connect" in state:
                raise MQMIError(2, state["raise_at_connect"])

        def commit(self) -> None:
            state["commits"] = state.get("commits", 0) + 1

        def disconnect(self) -> None:
            state["disconnected"] = True

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
            state.setdefault("replies", []).append((self.od, md, body, pmo))

        def close(self) -> None:
            pass

    mod = types.ModuleType("pymqi")
    mod.MQMIError = MQMIError
    mod.CMQC = CMQC
    mod.CD = mod.MD = mod.OD = mod.GMO = mod.PMO = _Struct
    mod.SCO = SCO
    mod.QueueManager = QueueManager
    mod.Queue = Queue
    return mod


def _run(monkeypatch, state: dict, *extra: str) -> int:
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state))
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
    assert (name, cd.ChannelName, cd.ConnectionName) == (
        "SVCQM",
        b"SVC.SVRCONN",
        b"localhost(1414)",
    )
    assert sco is None  # no --keyrepo -> plaintext connect


def test_mq_error_is_logged_and_exits_nonzero(monkeypatch, capsys):
    state: dict = {"requests": [], "raise_at_get": BROKEN}
    assert _run(monkeypatch, state) == 1
    assert "reason 2009" in capsys.readouterr().err
    assert state["disconnected"]


def test_connect_error_is_logged_and_exits_nonzero(monkeypatch, capsys):
    state: dict = {"requests": [], "raise_at_connect": 2059}
    assert _run(monkeypatch, state) == 1
    assert "reason 2059" in capsys.readouterr().err


def test_tls_sets_cipher_keyrepo_and_cert_label(monkeypatch):
    state: dict = {"requests": []}
    assert _run(monkeypatch, state, "--keyrepo", "/var/mqm/ssl/k", "--certlabel", "svc") == 0
    _name, cd, sco = state["connect"]
    assert cd.SSLCipherSpec == b"ANY_TLS13_OR_HIGHER"
    assert sco.KeyRepository == b"/var/mqm/ssl/k"
    assert sco.CertificateLabel == b"svc"


def test_tls_without_cert_label_leaves_the_default(monkeypatch):
    state: dict = {"requests": []}
    assert _run(monkeypatch, state, "--keyrepo", "/var/mqm/ssl/k") == 0
    _name, _cd, sco = state["connect"]
    assert sco.KeyRepository == b"/var/mqm/ssl/k"
    assert not hasattr(sco, "CertificateLabel")
