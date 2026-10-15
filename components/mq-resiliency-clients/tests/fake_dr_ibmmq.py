"""A fake ibmmq for the DR flow clients (mq-dr-flow, mq-dr-responder; #1550).

It models only what their correctness rests on, so tests can assert it without a queue
manager:

- **Message matching.** A GET honours ``GMO.MatchOptions`` against the MD passed in, as
  MQ does: with MQMO_MATCH_CORREL_ID (or MSG_ID) set, a non-null id in the MD selects
  only messages carrying that id; a null id (MQCI_NONE / MQMI_NONE) matches any. The
  default MatchOptions is MSG_ID | CORREL_ID, MQ's own default. The GET fills the MD
  with the message's ids -- which is why a reused MD would match only that message.
- **Syncpoint.** Puts become visible at commit; a got message is restored to the head
  of its queue on backout (or on a failed commit). A normal disconnect does NOT commit
  here -- the tests assert the explicit backout/commit calls instead.
- **Faults.** ``Broker.faults[key]`` is a list of reason codes; each call of that kind
  pops one and raises MQMIError. Keys: ``connect``, ``get:<queue>``, ``put:<queue>``,
  ``commit:put`` (a unit of work holding a put), ``commit:get`` (gets only),
  ``backout``, ``disconnect``.
- **A responder.** ``Broker.on_commit_put(queue, msg)`` runs for every message a commit
  publishes, so a test can stand in for the far side of a request/reply flow.

The MQ numbers are the real MQRC / MQMO / MQFB values; tests/test_ibmmq_refs.py checks
the clients' constant names against the real binding.
"""

from __future__ import annotations

import itertools
import threading
import time
import types
from collections.abc import Callable
from typing import Any

NONE24 = b"\0" * 24  # MQMI_NONE / MQCI_NONE
NO_MSG = 2033  # MQRC_NO_MSG_AVAILABLE
DLH_BYTES = b"<MQDLH>"
DEADQ = "SYSTEM.DEAD.LETTER.QUEUE"


class MQMIError(Exception):
    def __init__(self, comp: int, reason: int) -> None:
        self.comp = comp
        self.reason = reason
        super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")


class CMQC:
    MQGMO_WAIT = 0x1
    MQGMO_SYNCPOINT = 0x2
    MQGMO_FAIL_IF_QUIESCING = 0x2000
    MQPMO_SYNCPOINT = 0x2
    MQPMO_NEW_MSG_ID = 0x40
    MQPMO_FAIL_IF_QUIESCING = 0x2000
    MQMO_MATCH_MSG_ID = 0x1
    MQMO_MATCH_CORREL_ID = 0x2
    MQPER_PERSISTENT = 1
    MQEI_UNLIMITED = -1
    MQOO_OUTPUT = 0x10
    MQOO_FAIL_IF_QUIESCING = 0x2000
    MQRC_NO_MSG_AVAILABLE = NO_MSG
    MQRC_MISSING_REPLY_TO_Q = 2027
    MQFB_APPL_FIRST = 65536
    MQCA_DEAD_LETTER_Q_NAME = 2006
    MQFMT_DEAD_LETTER_HEADER = b"MQDEAD  "
    MQAT_UNIX = 6


class _Struct:
    """Stands in for MD / GMO / PMO / OD / DLH, with MQ's defaults for the fields used."""

    def __init__(self, **kw: Any) -> None:
        self.MsgId = NONE24
        self.CorrelId = NONE24
        self.Format = b"MQSTR   "
        self.Encoding = 546
        self.CodedCharSetId = 1208
        self.MsgType = 8
        self.Persistence = 0
        self.Priority = 0
        self.Expiry = -1
        self.Options = 0
        self.WaitInterval = 0
        self.MatchOptions = CMQC.MQMO_MATCH_MSG_ID | CMQC.MQMO_MATCH_CORREL_ID
        for key, value in kw.items():
            setattr(self, key, value)

    def pack(self) -> bytes:  # DLH
        return DLH_BYTES


class Broker:
    """One fake queue manager, shared by every connection a test opens."""

    def __init__(self, queues: dict[str, list[types.SimpleNamespace]] | None = None) -> None:
        self.queues: dict[str, list[types.SimpleNamespace]] = queues or {}
        self.deadq = DEADQ
        self.faults: dict[str, list[int]] = {}
        self.events: list[str] = []
        self.put_mds: list[_Struct] = []  # every MD object passed to put, in order
        self.get_mds: list[_Struct] = []  # every MD object passed to get, in order
        self.on_commit_put: Callable[[str, types.SimpleNamespace], None] | None = None
        self.lock = threading.RLock()
        self._ids = itertools.count(1)

    def fault(self, key: str) -> None:
        with self.lock:
            pending = self.faults.get(key)
            reason = pending.pop(0) if pending else None
        if reason is not None:
            raise MQMIError(2, reason)

    def new_msg_id(self) -> bytes:
        return f"MSG{next(self._ids)}".encode().ljust(24, b"\0")

    def connect(self, keep_going: Callable[[], bool]) -> QueueManager | None:
        if not keep_going():
            return None
        self.fault("connect")
        return QueueManager(self)

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("ibmmq")
        mod.MQMIError = MQMIError
        mod.CMQC = CMQC
        mod.MD = mod.GMO = mod.PMO = mod.OD = mod.DLH = _Struct
        mod.Queue = lambda qmgr, name=None: Queue(qmgr, name)
        return mod


def message(body: bytes, *, correl_id: bytes = NONE24, msg_id: bytes = NONE24, **md: Any):
    """A message as stored on a fake queue."""
    return types.SimpleNamespace(body=body, CorrelId=correl_id, MsgId=msg_id, md=md)


class QueueManager:
    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.uow: list[tuple[str, str, types.SimpleNamespace]] = []

    def _rollback(self) -> None:
        with self.broker.lock:
            for op, queue, msg in reversed(self.uow):
                if op == "get":
                    self.broker.queues.setdefault(queue, []).insert(0, msg)
            self.uow = []

    def commit(self) -> None:
        kind = "commit:put" if any(op == "put" for op, _q, _m in self.uow) else "commit:get"
        try:
            self.broker.fault(kind)
        except MQMIError:
            self._rollback()
            raise
        with self.broker.lock:
            published = [(q, m) for op, q, m in self.uow if op == "put"]
            for queue, msg in published:
                self.broker.queues.setdefault(queue, []).append(msg)
            self.uow = []
            self.broker.events.append("commit")
        for queue, msg in published:
            if self.broker.on_commit_put is not None:
                self.broker.on_commit_put(queue, msg)

    def backout(self) -> None:
        self.broker.events.append("backout")
        self._rollback()
        self.broker.fault("backout")

    def disconnect(self) -> None:
        self.broker.events.append("disconnect")
        self.broker.fault("disconnect")

    def inquire(self, selector: int) -> bytes:
        assert selector == CMQC.MQCA_DEAD_LETTER_Q_NAME
        return self.broker.deadq.encode().ljust(48)


class Queue:
    def __init__(self, qmgr: QueueManager, name: str | None) -> None:
        self.qmgr, self.name = qmgr, name

    def open(self, od: _Struct, _opts: int) -> None:
        self.name = od.ObjectName

    def close(self) -> None:
        pass

    def get(self, _buf: object, md: _Struct, gmo: _Struct) -> bytes:
        broker = self.qmgr.broker
        broker.get_mds.append(md)
        broker.fault(f"get:{self.name}")
        with broker.lock:
            queue = broker.queues.setdefault(self.name, [])
            for i, msg in enumerate(queue):
                by_correl = gmo.MatchOptions & CMQC.MQMO_MATCH_CORREL_ID and md.CorrelId != NONE24
                by_msg_id = gmo.MatchOptions & CMQC.MQMO_MATCH_MSG_ID and md.MsgId != NONE24
                if (by_correl and msg.CorrelId != md.CorrelId) or (
                    by_msg_id and msg.MsgId != md.MsgId
                ):
                    continue
                del queue[i]
                self.qmgr.uow.append(("get", self.name, msg))
                md.MsgId, md.CorrelId = msg.MsgId, msg.CorrelId
                for key, value in msg.md.items():
                    setattr(md, key, value)
                return msg.body
        time.sleep(0.002)  # the real GET waits; don't busy-spin the test
        raise MQMIError(2, NO_MSG)

    def put(self, body: bytes, md: _Struct, pmo: _Struct) -> None:
        broker = self.qmgr.broker
        broker.put_mds.append(md)
        broker.fault(f"put:{self.name}")
        msg_id = md.MsgId
        if msg_id == NONE24 or pmo.Options & CMQC.MQPMO_NEW_MSG_ID:
            msg_id = broker.new_msg_id()
        msg = message(
            body,
            correl_id=md.CorrelId,
            msg_id=msg_id,
            Persistence=md.Persistence,
            Format=md.Format,
        )
        msg.put_md, msg.pmo = md, pmo
        self.qmgr.uow.append(("put", self.name, msg))
