"""Firm-side continuous flow generator (persistent + syncpoint, HA-aware).

Producer: build_body() -> MQPUT (PERSISTENT, FAIL_IF_QUIESCING) under syncpoint
          -> commit -> firm ledger SENT.
Consumer: MQGET reply (FAIL_IF_QUIESCING) under syncpoint -> commit -> firm
          ledger CONFIRMED.

Producer and consumer run on SEPARATE MQ connections (a single Hconn is not safe
to share across threads -- MQRC_HCONN_ERROR); the ledger is shared under a lock.

Each side wraps its work in an explicit reconnect loop (see dr_mqi.py): it
cooperates with a controlled endmqm via FAIL_IF_QUIESCING, retries in-doubt
operations against the same business key, and rebuilds the connection itself
when a controlled endmqm -w disconnects it non-reconnectably (auto-reconnect
only covers abrupt breaks). The producer carries the in-flight message's
seq/uuid across a rebuild so a failover never drops or silently re-keys it.

Target QM / connection / queues are parameterized so the same client drives any
arm: the message path (QMAIN @ 10.30.0.10) or an HA arm via its VIP, e.g.
    ~/mqvenv/bin/python ~/dr_flow.py --qm QMPCMK --conn "10.10.1.200(1414)" \
        --req-queue DR.REQUEST --reply-queue DR.REPLY \
        --rate 20 --seconds 30 --ledger ~/dr-ledgers/app.jsonl

Deployed to lab nodes by ansible alongside mqlab/ and dr_mqi.py.
"""

import argparse
import pathlib
import threading
import time
import uuid as uuidlib

import dr_mqi
import pymqi
from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import build_body, parse_body
from mqlab.header import pack_header

STOP = threading.Event()
DRAIN_SECONDS = 4.0


def _parse_reply(raw):
    # the reply echoes the DRv1 body after the FFH header; slice from the marker
    idx = raw.find(b"DRv1|")
    return parse_body(raw[idx:])


def producer(c, rate, seconds, expiry, req_queue, ledger, lock, ledger_path):
    deadline = time.monotonic() + seconds
    keep = lambda: not STOP.is_set() and time.monotonic() < deadline  # noqa: E731
    last_flush = time.monotonic()
    pmo = pymqi.PMO(
        Options=pymqi.CMQC.MQPMO_SYNCPOINT | pymqi.CMQC.MQPMO_FAIL_IF_QUIESCING
    )
    expiry_tenths = pymqi.CMQC.MQEI_UNLIMITED if expiry is None else int(expiry * 10)
    md = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT, Expiry=expiry_tenths)
    interval = 1.0 / rate

    qmgr, q, seq = None, None, 0
    pending = None  # (seq, uuid, payload) built but not yet confirmed-sent
    while keep():
        if qmgr is None:
            qmgr = dr_mqi.connect_retry(c.qm, c.conn, c.channel, keep)
            if qmgr is None:
                break
            q = pymqi.Queue(qmgr, req_queue)
        if pending is None:
            seq += 1
            u = uuidlib.uuid4().hex
            body = build_body(seq=seq, uuid=u, session_date="20260608", payload=f"MSG-{seq}")
            header = pack_header(
                password="pw", sender="APP01", receiver="SVC", session_date="20260608"
            ).encode()
            pending = (seq, u, header + body)
        pseq, puuid, payload = pending
        try:
            q.put(payload, md, pmo)
            qmgr.commit()
        except pymqi.MQMIError as e:
            if e.reason in dr_mqi.RECONNECT:
                # Controlled endmqm disconnected us non-reconnectably: drop the
                # dead connection and rebuild. `pending` is kept so the SAME
                # seq/uuid is resent (the broken commit did not land) -- no loss.
                try:
                    qmgr.disconnect()
                except pymqi.MQMIError:
                    pass
                qmgr, q = None, None
                time.sleep(0.5)
                continue
            if e.reason == dr_mqi.CALL_INTERRUPTED:
                # 2549: commit outcome IN DOUBT -- the message may already have
                # landed. Do NOT back out; retry the SAME seq/uuid so a
                # double-landing is a detectable duplicate (classifier's
                # Duplicated bucket), never a silent loss.
                time.sleep(0.5)
                continue
            if e.reason in dr_mqi.RETRY_INPLACE:
                # 2003/2161: clean rollback. Back out the stuck UOW so the next
                # commit starts fresh, then retry the same seq.
                try:
                    qmgr.backout()
                except pymqi.MQMIError:
                    pass
                time.sleep(0.5)
                continue
            raise
        with lock:  # record SENT only after a clean commit
            ledger.append(LedgerEntry(Event.SENT, pseq, puuid, time.time()))
        pending = None
        # Periodically flush the (shared) ledger to disk so a client that later
        # hangs on a dead VIP -- MQCONNX blocks past the deadline when the site
        # is gone -- still leaves its evidence behind (write-at-exit alone loses
        # everything on a hang/kill).
        if time.monotonic() - last_flush > 2.0:
            with lock:
                ledger.write_jsonl(ledger_path)
            last_flush = time.monotonic()
        time.sleep(interval)

    if qmgr is not None:
        try:
            qmgr.disconnect()
        except pymqi.MQMIError:
            pass


def consumer(c, reply_queue, ledger, lock):
    keep = lambda: not STOP.is_set()  # noqa: E731
    # FAIL_IF_QUIESCING: the blocking MQGET-WAIT must notice a controlled endmqm
    # quiesce instead of hanging through it.
    gmo = pymqi.GMO(
        Options=(
            pymqi.CMQC.MQGMO_SYNCPOINT
            | pymqi.CMQC.MQGMO_WAIT
            | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=2000,
    )
    qmgr, q = None, None
    while keep():
        if qmgr is None:
            qmgr = dr_mqi.connect_retry(c.qm, c.conn, c.channel, keep)
            if qmgr is None:
                break
            q = pymqi.Queue(qmgr, reply_queue)
        try:
            raw = q.get(None, pymqi.MD(), gmo)
        except pymqi.MQMIError as e:
            if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            if e.reason in dr_mqi.RECONNECT:
                try:
                    qmgr.disconnect()
                except pymqi.MQMIError:
                    pass
                qmgr, q = None, None
                time.sleep(0.5)
                continue
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                time.sleep(0.2)  # yield to the reconnect thread; no busy-spin
                continue
            raise
        msg = _parse_reply(raw)
        try:
            qmgr.commit()
        except pymqi.MQMIError as e:
            if e.reason in dr_mqi.RECONNECT:
                try:
                    qmgr.disconnect()
                except pymqi.MQMIError:
                    pass
                qmgr, q = None, None
                time.sleep(0.5)
                continue
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                # outcome in doubt: don't record CONFIRMED. If it did not commit
                # the reply is requeued and we re-get it; if it did, the seq
                # stays sent-but-unconfirmed and the reconciler honestly buckets
                # it as Ambiguous rather than claiming an unsure confirmation.
                time.sleep(0.2)
                continue
            raise
        with lock:  # record CONFIRMED only after a clean commit
            ledger.append(LedgerEntry(Event.CONFIRMED, msg.seq, msg.uuid, time.time()))

    if qmgr is not None:
        try:
            qmgr.disconnect()
        except pymqi.MQMIError:
            pass


class _Conn:
    def __init__(self, conn, channel, qm):
        self.conn, self.channel, self.qm = conn, channel, qm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", default="QMAIN")
    ap.add_argument("--conn", default="10.30.0.10(1414)")
    ap.add_argument("--channel", default="APP.SVRCONN")
    ap.add_argument("--req-queue", default="DTCC.REQUEST")
    ap.add_argument("--reply-queue", default="TRADE.REPLY")
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument(
        "--expiry", type=float, default=None,
        help="per-message expiry in seconds (default: unlimited)",
    )
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    c = _Conn(args.conn, args.channel, args.qm)
    ledger, lock = Ledger(), threading.Lock()
    t = threading.Thread(
        target=consumer, args=(c, args.reply_queue, ledger, lock), daemon=True
    )
    t.start()
    producer(c, args.rate, args.seconds, args.expiry, args.req_queue, ledger, lock, args.ledger)
    time.sleep(DRAIN_SECONDS)  # let in-flight replies land before stopping
    STOP.set()
    t.join(timeout=5)
    with lock:
        ledger.write_jsonl(args.ledger)
    sent = len(ledger.sent_seqs())
    confirmed = len(ledger.confirmed_seqs())
    print(f"flow done: {sent} sent, {confirmed} confirmed -> {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
