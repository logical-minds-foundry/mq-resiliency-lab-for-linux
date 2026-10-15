"""Move a message a responder cannot handle to its queue manager's dead-letter queue.

Shared by mq-svc-responder (a request with a blank ReplyToQ, #1513) and mq-dr-responder
(a message on its probe queue with no DRv1 body, #1550): neither wedges its loop on such
a message nor silently drops it. The message goes to the DEADQ behind an MQDLH, under the
caller's syncpoint -- so it leaves the input queue in the same unit of work that puts it
on the DLQ -- and the move is logged to stderr. ibmmq is imported lazily, so the module
imports without the MQ client libs present (unit tests, the component selfcheck).
"""

from __future__ import annotations

import sys
import time
from typing import Any


class NoDeadLetterQueueError(Exception):
    """The queue manager has no DEADQ, so an unhandleable message cannot be moved aside."""


def mqchar(value: bytes | str) -> str:
    """An MQCHAR field as text, without its blank / NUL padding."""
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    return value.strip(" \x00")


def dead_letter(
    qmgr: Any,
    qmgr_name: str,
    in_queue: str,
    req_md: Any,
    body: bytes,
    *,
    reason: int,
    who: str,
    why: str,
) -> None:
    """Put `body` (read from `in_queue` with MQMD `req_md`) on the DEADQ behind an
    MQDLH carrying `reason`, under the caller's syncpoint; the caller commits.

    The MQDLH carries the original Encoding / CodedCharSetId / Format and DestQName =
    the queue it was read from; the dead-letter MQMD keeps its MsgId and CorrelId so it
    can be traced back. `who` names the program (PutApplName ``mq-<who>`` and the log
    prefix); `why` is the logged cause. Raises NoDeadLetterQueueError when the queue
    manager has no DEADQ.
    """
    import ibmmq

    dlq = mqchar(qmgr.inquire(ibmmq.CMQC.MQCA_DEAD_LETTER_Q_NAME))
    if not dlq:
        raise NoDeadLetterQueueError(
            f"{qmgr_name} has no DEADQ: cannot dead-letter a message ({why})"
        )
    now = time.gmtime()
    dlh = ibmmq.DLH(
        Reason=reason,
        DestQName=in_queue,
        DestQMgrName=qmgr_name,
        Encoding=req_md.Encoding,
        CodedCharSetId=req_md.CodedCharSetId,
        Format=req_md.Format,
        PutApplType=ibmmq.CMQC.MQAT_UNIX,
        PutApplName=f"mq-{who}",
        PutDate=time.strftime("%Y%m%d", now),
        PutTime=time.strftime("%H%M%S00", now),
    )
    dl_md = ibmmq.MD(
        MsgId=req_md.MsgId,
        CorrelId=req_md.CorrelId,
        MsgType=req_md.MsgType,
        Persistence=req_md.Persistence,
        Priority=req_md.Priority,
        Format=ibmmq.CMQC.MQFMT_DEAD_LETTER_HEADER,
    )
    pmo = ibmmq.PMO(Options=ibmmq.CMQC.MQPMO_SYNCPOINT | ibmmq.CMQC.MQPMO_FAIL_IF_QUIESCING)
    qdl = ibmmq.Queue(qmgr)
    qdl.open(ibmmq.OD(ObjectName=dlq), ibmmq.CMQC.MQOO_OUTPUT | ibmmq.CMQC.MQOO_FAIL_IF_QUIESCING)
    qdl.put(dlh.pack() + body, dl_md, pmo)
    qdl.close()
    msg_id = req_md.MsgId
    print(
        f"{who}: dead-lettered message {bytes(msg_id).hex()} from {in_queue} to {dlq}: "
        f"{why} (MQDLH reason {reason})",
        file=sys.stderr,
        flush=True,
    )
