"""SVC-side Watcher responder (syncpoint, HA-reconnect-aware).

Records RECEIVED for EVERY get (so a redelivered message counts as a duplicate,
spec §5) and REPLIED for every reply, into the Watcher ledger -- but only
AFTER a clean commit, so a failover rollback never logs a phantom receive.
Runs on the svc-sim node (outside both DC sites, so the oracle survives a full
site loss).

Connection handling embodies the client HA requirements (see dr_mqi.py): cooperate
with a controlled endmqm via FAIL_IF_QUIESCING, retry in-doubt operations, and --
crucially -- rebuild the connection ourselves when a controlled endmqm -w
disconnects us non-reconnectably (auto-reconnect only covers abrupt breaks).

Run on svc-sim:
    mq-dr-responder --seconds 40 --ledger ~/dr-ledgers/svc.jsonl

Installed as the `mq-dr-responder` console script of the mq-resiliency-clients
component. Echoes the DRv1 body back so the app can match seq/uuid, and copies the
request's CorrelId (mq-dr-flow's per-run id) onto the reply so mq-dr-flow takes only
its own replies off a shared reply queue (#1550).

A message on the input queue with no DRv1 body is not ours to answer (#1550): it is
moved, in the same unit of work, to the queue manager's dead-letter queue behind an
MQDLH (reason MQFB_APPL_FIRST, an application-defined rejection) and logged, so it
neither wedges the loop nor is silently dropped -- the mq-svc-responder pattern
(#1513, shared via mqrc.deadletter). A queue manager with no DEADQ is an error: the
unit of work is backed out and the process exits non-zero (fail loud).

ibmmq is imported lazily inside the MQI functions, so the module imports without
the MQ client libs present (unit tests, the component selfcheck).
"""

import argparse
import os
import pathlib
import sys
import time

from mqrc import dr_mqi
from mqrc.deadletter import NoDeadLetterQueueError, dead_letter
from mqrc.dr.ledger import Event, Ledger, LedgerEntry
from mqrc.dr.wire import NotDRv1Error, split_framed
from mqrc.header import pack_header

WHO = "dr-responder"


def _serve(qmgr, args, ledger, deadline):
    """Get/reply/commit loop on one connection. Returns when the deadline is
    reached; raises MQMIError(reason in dr_mqi.RECONNECT) when the connection is
    lost so the caller can rebuild it."""
    import ibmmq

    qin = ibmmq.Queue(qmgr, args.in_queue)
    qout = ibmmq.Queue(qmgr, args.out_queue)
    # FAIL_IF_QUIESCING: a blocking MQGET-WAIT must notice a controlled endmqm
    # quiesce instead of hanging through it.
    gmo = ibmmq.GMO(
        Options=(
            ibmmq.CMQC.MQGMO_SYNCPOINT | ibmmq.CMQC.MQGMO_WAIT | ibmmq.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=2000,
    )
    # NEW_MSG_ID: the QM generates a unique MsgId for every reply, even if a
    # descriptor were ever reused (#1549).
    pmo = ibmmq.PMO(
        Options=(
            ibmmq.CMQC.MQPMO_SYNCPOINT
            | ibmmq.CMQC.MQPMO_NEW_MSG_ID
            | ibmmq.CMQC.MQPMO_FAIL_IF_QUIESCING
        )
    )

    last_flush = time.monotonic()
    while time.monotonic() < deadline:
        # Fresh MD per get: a GET fills the MD, and a reused, populated one would
        # then match only that same MsgId/CorrelId.
        req_md = ibmmq.MD()
        try:
            raw = qin.get(None, req_md, gmo)
        except ibmmq.MQMIError as e:
            if e.reason == ibmmq.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                time.sleep(0.2)  # yield to the reconnect thread; no busy-spin
                continue
            raise  # dr_mqi.RECONNECT or unexpected -> caller rebuilds / fails loud
        try:
            body, msg = split_framed(raw)
        except NotDRv1Error as exc:
            # Not a DR message: dead-letter it in this unit of work, keep serving.
            try:
                dead_letter(
                    qmgr,
                    args.qm,
                    args.in_queue,
                    req_md,
                    raw,
                    reason=ibmmq.CMQC.MQFB_APPL_FIRST,
                    who=WHO,
                    why=str(exc),
                )
                qmgr.commit()
            except ibmmq.MQMIError as e:
                if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                    time.sleep(0.2)  # rolled back (or in doubt): re-get and retry it
                    continue
                raise
            continue
        reply = (
            pack_header(
                password="pw", sender="SVC", receiver="APP01", session_date=msg.session_date
            ).encode()
            + body  # echo the DRv1 body so the app can match seq/uuid
        )
        try:
            # A fresh MD per reply: MQPUT writes the generated MsgId back into the
            # descriptor, so a reused MD would resend it on every later reply (#1549).
            # The request's CorrelId (mq-dr-flow's run id) is copied so the app's
            # consumer can take only its own run's replies (#1550).
            reply_md = ibmmq.MD(Persistence=ibmmq.CMQC.MQPER_PERSISTENT, CorrelId=req_md.CorrelId)
            qout.put(reply, reply_md, pmo)
            qmgr.commit()
        except ibmmq.MQMIError as e:
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                # get+reply rolled back (or in doubt) across failover; the request
                # is requeued and we re-get it. Record nothing -- a clean commit
                # is the only thing that logs RECEIVED/REPLIED.
                time.sleep(0.2)
                continue
            raise
        # record RECEIVED + REPLIED only after a clean commit (recording before
        # would log a phantom receive on a failover rollback and inflate dups).
        ledger.append(LedgerEntry(Event.RECEIVED, msg.seq, msg.uuid, time.time()))
        ledger.append(LedgerEntry(Event.REPLIED, msg.seq, msg.uuid, time.time()))
        # Periodically flush to disk so a client that later hangs on a dead VIP
        # (MQCONNX blocks past the deadline when the site is gone) still leaves
        # the Watcher's ledger behind.
        if time.monotonic() - last_flush > 2.0:
            ledger.write_jsonl(args.ledger)
            last_flush = time.monotonic()


def _run(args, ledger, deadline):
    """Serve until the deadline, rebuilding the connection whenever a controlled
    endmqm disconnects us non-reconnectably. On any other error the open unit of
    work is backed out before the disconnect -- a normal MQDISC would COMMIT it and
    consume the message the error interrupted -- and the error is re-raised."""
    import ibmmq

    qmgr = None
    try:
        while time.monotonic() < deadline:
            qmgr = dr_mqi.connect_retry(
                args.qm,
                args.conn,
                args.channel,
                lambda: time.monotonic() < deadline,
                keyrepo=args.keyrepo,
                certlabel=args.certlabel,
            )
            if qmgr is None:
                break
            try:
                _serve(qmgr, args, ledger, deadline)
                break  # deadline reached cleanly inside _serve
            except ibmmq.MQMIError as e:
                if e.reason not in dr_mqi.RECONNECT:
                    raise
                # connection lost (controlled endmqm) -> drop it and rebuild
                try:
                    qmgr.disconnect()
                except ibmmq.MQMIError:
                    pass
                qmgr = None
                time.sleep(0.5)
    except BaseException:
        if qmgr is not None:
            try:
                qmgr.backout()
            except ibmmq.MQMIError as exc:  # logged; the original error is re-raised
                print(f"{WHO}: backout failed: {exc} (reason {exc.reason})", file=sys.stderr)
        raise
    finally:
        if qmgr is not None:
            try:
                qmgr.disconnect()
            except ibmmq.MQMIError:
                pass
        ledger.write_jsonl(args.ledger)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", default=os.environ.get("MQLAB_QM_SVC", "PCMKSVC"))
    ap.add_argument("--conn", default="localhost(1414)")
    ap.add_argument("--channel", default="SVC.SVRCONN")
    ap.add_argument("--in-queue", default="SVC.REQUEST")
    ap.add_argument("--out-queue", default="APP.REPLY")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--ledger", required=True)
    # TLS (#1377, mirrors app_requester #250): --keyrepo is the keystore *stem*; a
    # sibling .sth supplies the password (the clients use the .sth stash;
    # SCO.KeyRepoPassword adoption is deferred, epic .github#300 §9). Omit both -> plaintext.
    ap.add_argument(
        "--keyrepo", default="", help="keystore stem, e.g. /var/mqm/ssl/svc-responder/key"
    )
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    args = ap.parse_args(argv)
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    ledger = Ledger()
    try:
        _run(args, ledger, time.monotonic() + args.seconds)
    except NoDeadLetterQueueError as exc:  # logged, non-zero, unit of work backed out
        print(f"{WHO}: {exc}", file=sys.stderr)
        return 1

    received = len(ledger.svc_receive_counts())
    print(f"responder done: {received} received -> {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
