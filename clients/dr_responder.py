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
    ~/mqvenv/bin/python ~/dr_responder.py --seconds 40 --ledger ~/dr-ledgers/svc.jsonl

Deployed by ansible alongside mqlab/ and dr_mqi.py. Echoes the DRv1 body back so
the app can match seq/uuid.
"""

import argparse
import os
import pathlib
import time

import dr_mqi
import pymqi
from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import parse_body
from mqlab.header import pack_header


def _serve(qmgr, args, ledger, deadline):
    """Get/reply/commit loop on one connection. Returns when the deadline is
    reached; raises MQMIError(reason in dr_mqi.RECONNECT) when the connection is
    lost so the caller can rebuild it."""
    qin = pymqi.Queue(qmgr, args.in_queue)
    qout = pymqi.Queue(qmgr, args.out_queue)
    # FAIL_IF_QUIESCING: a blocking MQGET-WAIT must notice a controlled endmqm
    # quiesce instead of hanging through it.
    gmo = pymqi.GMO(
        Options=(
            pymqi.CMQC.MQGMO_SYNCPOINT
            | pymqi.CMQC.MQGMO_WAIT
            | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=2000,
    )
    pmo = pymqi.PMO(
        Options=pymqi.CMQC.MQPMO_SYNCPOINT | pymqi.CMQC.MQPMO_FAIL_IF_QUIESCING
    )
    md_persist = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT)

    last_flush = time.monotonic()
    while time.monotonic() < deadline:
        try:
            raw = qin.get(None, pymqi.MD(), gmo)
        except pymqi.MQMIError as e:
            if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                time.sleep(0.2)  # yield to the reconnect thread; no busy-spin
                continue
            raise  # dr_mqi.RECONNECT or unexpected -> caller rebuilds / fails loud
        idx = raw.find(b"DRv1|")
        msg = parse_body(raw[idx:])
        reply = (
            pack_header(
                password="pw", sender="SVC", receiver="APP01", session_date=msg.session_date
            ).encode()
            + raw[idx:]  # echo the DRv1 body so the app can match seq/uuid
        )
        try:
            qout.put(reply, md_persist, pmo)
            qmgr.commit()
        except pymqi.MQMIError as e:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", default=os.environ.get("MQLAB_QM_SVC", "PCMKSVC"))
    ap.add_argument("--conn", default="localhost(1414)")
    ap.add_argument("--channel", default="SVC.SVRCONN")
    ap.add_argument("--in-queue", default="SVC.REQUEST")
    ap.add_argument("--out-queue", default="APP.REPLY")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    ledger = Ledger()
    deadline = time.monotonic() + args.seconds
    qmgr = None
    try:
        # Outer reconnect loop: rebuild the connection whenever a controlled
        # endmqm disconnects us non-reconnectably, until the deadline.
        while time.monotonic() < deadline:
            qmgr = dr_mqi.connect_retry(
                args.qm, args.conn, args.channel, lambda: time.monotonic() < deadline
            )
            if qmgr is None:
                break
            try:
                _serve(qmgr, args, ledger, deadline)
                break  # deadline reached cleanly inside _serve
            except pymqi.MQMIError as e:
                if e.reason not in dr_mqi.RECONNECT:
                    raise
                # connection lost (controlled endmqm) -> drop it and rebuild
                try:
                    qmgr.disconnect()
                except pymqi.MQMIError:
                    pass
                qmgr = None
                time.sleep(0.5)
    finally:
        if qmgr is not None:
            try:
                qmgr.disconnect()
            except pymqi.MQMIError:
                pass
        ledger.write_jsonl(args.ledger)

    received = len(ledger.svc_receive_counts())
    print(f"responder done: {received} received -> {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
