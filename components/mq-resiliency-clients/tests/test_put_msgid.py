"""Every DR and reconnect-probe put gets a unique MsgId (#1549).

MQPUT writes the queue-manager-generated MsgId back into the MQMD. A client that
reuses one MD across its put loop therefore resends that MsgId on every later put,
so all of its messages share one MsgId. Each site must build a fresh MD per put
(MsgId == MQMI_NONE going in) and set MQPMO_NEW_MSG_ID as belt-and-braces.

The fake ibmmq here mimics that write-back: every put records the descriptor it was
handed (and the MsgId it carried in), then stamps a new generated MsgId into it.
"""

from __future__ import annotations

import itertools
import sys
import threading
import types

import pytest

from mqrc import dr_flow, dr_responder, reconnect_probe
from mqrc.dr.ledger import Ledger
from mqrc.dr.wire import build_body

MQMI_NONE = b"\x00" * 24
MQCI_NONE = b"\x00" * 24
BROKEN = 2009  # MQRC_CONNECTION_BROKEN (in dr_mqi.RECONNECT)


def _fake_ibmmq(puts: list[dict], gets: list[bytes]) -> types.ModuleType:
    """`puts` collects one record per put; `gets` is the in-queue the responder
    drains, after which a GET reports a broken connection."""
    counter = itertools.count(1)

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason

    class CMQXC:
        MQXPT_TCP = 2

    class CMQC:
        MQCNO_CLIENT_BINDING = 0x800
        MQCNO_RECONNECT_Q_MGR = 0x4000000
        MQGMO_SYNCPOINT = 0x2
        MQGMO_WAIT = 0x1
        MQGMO_FAIL_IF_QUIESCING = 0x2000
        MQPMO_SYNCPOINT = 0x2
        MQPMO_NEW_MSG_ID = 0x40
        MQPMO_NEW_CORREL_ID = 0x80
        MQPMO_FAIL_IF_QUIESCING = 0x2000
        MQPER_PERSISTENT = 1
        MQEI_UNLIMITED = -1
        MQRC_NO_MSG_AVAILABLE = 2033

    class _Struct:  # stands in for CD / MD / GMO / PMO
        def __init__(self, **kw: object) -> None:
            self.MsgId = MQMI_NONE
            self.CorrelId = MQCI_NONE
            for key, value in kw.items():
                setattr(self, key, value)

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, *_a: object, **_kw: object) -> None:
            pass

        def commit(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

    class Queue:
        def __init__(self, _qmgr: object, name: str) -> None:
            self.name = name

        def put(self, body: bytes, md: _Struct, pmo: _Struct) -> None:
            puts.append(
                {
                    "md": md,
                    "msgid_in": md.MsgId,
                    "correlid_in": md.CorrelId,
                    "options": pmo.Options,
                    "body": body,
                },
            )
            # MQPUT is input/output on the MD: the generated ids are written back.
            n = next(counter)
            md.MsgId = n.to_bytes(24, "big")
            md.CorrelId = (1000 + n).to_bytes(24, "big")

        def get(self, _buf: object, _md: _Struct, _gmo: object) -> bytes:
            if gets:
                return gets.pop(0)
            raise MQMIError(2, BROKEN)

    mod = types.ModuleType("ibmmq")
    mod.MQMIError = MQMIError
    mod.CMQC = CMQC
    mod.CMQXC = CMQXC
    mod.CD = _Struct
    mod.MD = _Struct
    mod.GMO = _Struct
    mod.PMO = _Struct
    mod.QueueManager = QueueManager
    mod.Queue = Queue
    return mod


def _assert_fresh_descriptors(puts: list[dict], fake: types.ModuleType) -> None:
    assert len(puts) == 2
    first, second = puts
    assert first["md"] is not second["md"]
    for put in puts:
        assert put["msgid_in"] == MQMI_NONE  # never a previous put's written-back MsgId
        assert put["options"] & fake.CMQC.MQPMO_NEW_MSG_ID
        assert put["md"].Persistence == fake.CMQC.MQPER_PERSISTENT


def test_dr_flow_producer_puts_each_request_with_a_fresh_md(monkeypatch, tmp_path):
    puts: list[dict] = []
    fake = _fake_ibmmq(puts, [])
    monkeypatch.setitem(sys.modules, "ibmmq", fake)
    monkeypatch.setattr(dr_flow.time, "sleep", lambda _s: None)

    class _Conn:
        def connect_retry(self, _keep):
            qmgr = fake.QueueManager(None)
            commits = []

            def commit() -> None:
                commits.append(1)
                if len(commits) == 2:
                    dr_flow.STOP.set()  # two puts are enough to prove the point

            qmgr.commit = commit
            return qmgr

    try:
        dr_flow.producer(
            _Conn(),
            1000.0,
            60.0,
            5.0,
            "DR.REQUEST",
            Ledger(),
            threading.Lock(),
            str(tmp_path / "app.jsonl"),
        )
    finally:
        dr_flow.STOP.clear()  # module-global: don't leak a set STOP into other tests
    _assert_fresh_descriptors(puts, fake)
    assert all(put["md"].Expiry == 50 for put in puts)  # expiry kept, in tenths
    # No correlation option here: #1550 sets a per-run CorrelId on these requests.
    assert not any(put["options"] & fake.CMQC.MQPMO_NEW_CORREL_ID for put in puts)


def test_dr_responder_puts_each_reply_with_a_fresh_md(monkeypatch, tmp_path):
    puts: list[dict] = []
    requests = [
        build_body(seq=seq, uuid=f"u{seq}", session_date="20260608", payload=f"MSG-{seq}")
        for seq in (1, 2)
    ]
    fake = _fake_ibmmq(puts, requests)
    monkeypatch.setitem(sys.modules, "ibmmq", fake)
    args = types.SimpleNamespace(
        in_queue="DR.REQUEST",
        out_queue="DR.REPLY",
        ledger=str(tmp_path / "svc.jsonl"),
    )
    # once the two requests are served the fake breaks the connection, which _serve
    # raises for main() to rebuild
    with pytest.raises(fake.MQMIError):
        dr_responder._serve(fake.QueueManager(None), args, Ledger(), float("inf"))
    _assert_fresh_descriptors(puts, fake)
    # No correlation option here: #1550 copies the request CorrelId onto the reply.
    assert not any(put["options"] & fake.CMQC.MQPMO_NEW_CORREL_ID for put in puts)


def test_reconnect_probe_puts_each_message_with_a_fresh_md(monkeypatch, capsys):
    puts: list[dict] = []
    fake = _fake_ibmmq(puts, [])
    monkeypatch.setitem(sys.modules, "ibmmq", fake)
    monkeypatch.setattr(
        reconnect_probe, "connect_client", lambda *_a, **_kw: fake.QueueManager(None)
    )
    reconnect_probe.probe("PCMKAPP", "vip(1414)", "SVC.REQUEST", puts=2, interval=0)
    _assert_fresh_descriptors(puts, fake)
    for put in puts:
        assert put["correlid_in"] == MQCI_NONE
        assert put["options"] & fake.CMQC.MQPMO_NEW_CORREL_ID
    assert "put 1 ok" in capsys.readouterr().out
