"""SVC service responder -- the counterparty (Business B) side of the QM-to-QM flow.

Runs co-located with the SVC QM on the SVC service VM in BINDINGS mode (a local
connection, no client channel). Reads each request from SVC.REQUEST, sends a reply
to the request's ReplyToQ / ReplyToQMgr, and honours the request/reply correlation
contract so the requesting app can match it:

  * reply CorrelId = request MsgId   (the match key the app GETs by)
  * MQPMO_NEW_MSG_ID                 (the reply gets its own MsgId)

Get + put run under syncpoint with a single commit, so a crash never loses a
request or emits a duplicate reply. MQI errors are never swallowed -- the unit of work
is backed out (a normal MQDISC would COMMIT it and consume the request, #1513), the
error is logged to stderr and the process exits non-zero (fail loud) and systemd
restarts it.

A message it cannot answer -- a blank ReplyToQ, e.g. a mq-dr-flow probe datagram sent
into the live service queue (#1513) -- is not a crash: it is moved, in the same unit of
work, to the queue manager's dead-letter queue behind an MQDLH with reason
MQRC_MISSING_REPLY_TO_Q (2027), and logged to stderr. Before #1513 the reply open failed
2184 on every restart. A queue manager with no DEADQ is an error (fail loud). MsgType is
not checked: mq-app-requester sends its requests as datagrams with a ReplyToQ.

Installed as the `mq-svc-responder` console script of the mq-resiliency-clients
component and run by its `mq-svc-responder@.service` unit (#147). It serves forever
unless `--seconds` bounds the run (tests, ad-hoc drills). ibmmq is imported lazily
inside the MQI functions, so the module imports without the MQ client libs present
(unit tests, the component selfcheck).
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from mqrc.mqconn import connect_client


def serve(
    qmgr_name: str,
    in_queue: str,
    channel: str,
    conn: str,
    keyrepo: str = "",
    certlabel: str = "",
    seconds: float | None = None,
) -> None:
    """Get/reply/commit on a client connection to `qmgr_name` -- forever, or until
    `seconds` have elapsed when it is given.

    Client-mode to localhost -- the QM is co-located, but the responder deliberately
    connects client-mode over the TLS SVC.SVRCONN (#180); passing an MQCD makes ibmmq
    connect as a client. A loopback client connection is local-in-spirit and matches
    dr_responder.py.

    TLS (#250): when `keyrepo` is given (the keystore stem, no .p12), connect with
    mutual TLS 1.3 -- a sibling .sth stash supplies the PKCS#12 password and
    `certlabel` selects the cert. Omit it for a plaintext connect.
    """
    import ibmmq

    deadline = None if seconds is None else time.monotonic() + seconds
    cd = ibmmq.CD(
        ChannelName=channel,
        ConnectionName=conn,
        TransportType=ibmmq.CMQXC.MQXPT_TCP,
    )
    sco = None
    if keyrepo:
        cd.SSLCipherSpec = "ANY_TLS13_OR_HIGHER"
        sco = ibmmq.SCO(KeyRepository=keyrepo)
        if certlabel:
            sco.CertificateLabel = certlabel
    qmgr = connect_client(qmgr_name, cd, sco)
    qin = ibmmq.Queue(qmgr, in_queue)
    gmo = ibmmq.GMO(
        Options=(
            ibmmq.CMQC.MQGMO_SYNCPOINT | ibmmq.CMQC.MQGMO_WAIT | ibmmq.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=5000,
    )
    pmo = ibmmq.PMO(
        Options=(
            ibmmq.CMQC.MQPMO_SYNCPOINT
            | ibmmq.CMQC.MQPMO_NEW_MSG_ID
            | ibmmq.CMQC.MQPMO_FAIL_IF_QUIESCING
        )
    )
    try:
        while deadline is None or time.monotonic() < deadline:
            req_md = ibmmq.MD()
            try:
                body = qin.get(None, req_md, gmo)
            except ibmmq.MQMIError as exc:
                if exc.reason == ibmmq.CMQC.MQRC_NO_MSG_AVAILABLE:
                    continue
                raise  # fail loud -- never swallow an MQI error
            if not _mqchar(req_md.ReplyToQ):
                # Unanswerable (#1513): dead-letter it in this unit of work, keep serving.
                _dead_letter(qmgr, qmgr_name, in_queue, req_md, body)
                qmgr.commit()
                continue
            reply_md = ibmmq.MD(
                CorrelId=req_md.MsgId,  # the correlation contract
                Persistence=ibmmq.CMQC.MQPER_PERSISTENT,
                Format=req_md.Format,
            )
            reply_od = ibmmq.OD(
                ObjectName=req_md.ReplyToQ,
                ObjectQMgrName=req_md.ReplyToQMgr,  # resolves to the xmitq -> SENDER
            )
            qout = ibmmq.Queue(qmgr)
            qout.open(reply_od, ibmmq.CMQC.MQOO_OUTPUT | ibmmq.CMQC.MQOO_FAIL_IF_QUIESCING)
            qout.put(b"REPLY:" + body, reply_md, pmo)
            qout.close()
            qmgr.commit()
    except BaseException:
        # A normal MQDISC COMMITS an open unit of work, so without this backout the
        # disconnect below would consume the request the error interrupted (#1513).
        try:
            qmgr.backout()
        except ibmmq.MQMIError as exc:  # logged; the original error is re-raised below
            print(f"svc-responder: backout failed: {exc} (reason {exc.reason})", file=sys.stderr)
        raise
    finally:
        qmgr.disconnect()


class NoDeadLetterQueueError(Exception):
    """The queue manager has no DEADQ, so an unanswerable message cannot be moved aside."""


def _mqchar(value: bytes | str) -> str:
    """An MQCHAR field as text, without its blank / NUL padding."""
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    return value.strip(" \x00")


def _dead_letter(qmgr: Any, qmgr_name: str, in_queue: str, req_md: Any, body: bytes) -> None:
    """Move a message with a blank ReplyToQ to the queue manager's dead-letter queue,
    behind an MQDLH (reason MQRC_MISSING_REPLY_TO_Q), under the caller's syncpoint.

    The MQDLH carries the original Encoding / CodedCharSetId / Format and DestQName =
    the queue it was read from; the dead-letter MQMD keeps its MsgId and CorrelId so it
    can be traced back. Raises NoDeadLetterQueueError when the queue manager has no DEADQ.
    """
    import ibmmq

    dlq = _mqchar(qmgr.inquire(ibmmq.CMQC.MQCA_DEAD_LETTER_Q_NAME))
    if not dlq:
        raise NoDeadLetterQueueError(
            f"{qmgr_name} has no DEADQ: cannot dead-letter a message with a blank ReplyToQ"
        )
    now = time.gmtime()
    dlh = ibmmq.DLH(
        Reason=ibmmq.CMQC.MQRC_MISSING_REPLY_TO_Q,
        DestQName=in_queue,
        DestQMgrName=qmgr_name,
        Encoding=req_md.Encoding,
        CodedCharSetId=req_md.CodedCharSetId,
        Format=req_md.Format,
        PutApplType=ibmmq.CMQC.MQAT_UNIX,
        PutApplName="mq-svc-responder",
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
        f"svc-responder: dead-lettered message {bytes(msg_id).hex()} from {in_queue} to {dlq}: "
        f"blank ReplyToQ (MQRC {ibmmq.CMQC.MQRC_MISSING_REPLY_TO_Q})",
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    import ibmmq

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qm", default="PCMKSVC")
    ap.add_argument("--in-queue", default="SVC.REQUEST")
    ap.add_argument("--channel", default="SVC.SVRCONN")
    ap.add_argument("--conn", default="localhost(1414)")
    # TLS (#250): keystore stem + cert label; omit both for a plaintext connect.
    ap.add_argument(
        "--keyrepo", default="", help="keystore stem, e.g. /var/mqm/ssl/svc-responder/key"
    )
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    ap.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="stop after this many seconds (default: forever)",
    )
    args = ap.parse_args(argv)
    try:
        serve(
            args.qm,
            args.in_queue,
            args.channel,
            args.conn,
            args.keyrepo,
            args.certlabel,
            args.seconds,
        )
    except ibmmq.MQMIError as exc:  # logged, then a non-zero exit -- never swallowed
        print(f"svc-responder: MQ error: {exc} (reason {exc.reason})", file=sys.stderr)
        return 1
    except NoDeadLetterQueueError as exc:  # the same: logged, non-zero, unit backed out
        print(f"svc-responder: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
