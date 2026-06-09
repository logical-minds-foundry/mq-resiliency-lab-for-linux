"""Firm-side continuous flow generator (persistent + syncpoint).

Producer: build_body() -> MQPUT (PERSISTENT) under syncpoint -> commit ->
          firm ledger SENT.
Consumer: MQGET reply (FAIL_IF_QUIESCING) under syncpoint -> commit ->
          firm ledger CONFIRMED.

Producer and consumer run on SEPARATE MQ connections — a single Hconn is not
safe to share across threads (MQRC_HCONN_ERROR). The ledger is shared under a
lock. The producer runs to its deadline and returns; main then drains in-flight
replies for a few seconds before signalling the consumer to stop.

Target QM / connection / queues are parameterized so the same client drives any
arm: the message path (QMAIN @ 10.30.0.10) or an HA arm via its VIP, e.g.
    ~/mqvenv/bin/python ~/dr_flow.py --qm QMPCMK --conn "10.10.1.200(1414)" \
        --req-queue DR.REQUEST --reply-queue DR.REPLY \
        --rate 20 --seconds 30 --ledger ~/dr-ledgers/firm.jsonl

Deployed to lab nodes by ansible alongside mqlab/ (the dr framework package).
"""

import argparse
import pathlib
import threading
import time
import uuid as uuidlib

import pymqi
from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import build_body, parse_body
from mqlab.epn import pack_header

STOP = threading.Event()
DRAIN_SECONDS = 4.0

# MQI reason codes meaning "an HA failover backed out the in-flight unit of work"
# — retry the operation (record the ledger entry only on a clean commit) rather
# than dying. This is what lets the flow ride through a failover under load.
_RECONNECT_BACKOUT = (
    pymqi.CMQC.MQRC_BACKED_OUT,
    pymqi.CMQC.MQRC_CALL_INTERRUPTED,
)


def _connect(conn, channel, qm):
    cd = pymqi.CD(
        ChannelName=channel.encode(),
        ConnectionName=conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    qmgr = pymqi.QueueManager(None)
    # MQCNO_RECONNECT_Q_MGR: the client library transparently reconnects to the
    # same QM (via the VIP) across an HA failover, so the flow survives the fault
    # and continues — the whole point of the under-load drills.
    qmgr.connect_with_options(qm, cd=cd, opts=pymqi.CMQC.MQCNO_RECONNECT_Q_MGR)
    return qmgr


def _parse_reply(raw):
    # the reply echoes the DRv1 body after the EPN header; slice from the marker
    idx = raw.find(b"DRv1|")
    return parse_body(raw[idx:])


def producer(c, rate, seconds, expiry, req_queue, ledger, lock):
    qmgr = _connect(c.conn, c.channel, c.qm)
    try:
        q = pymqi.Queue(qmgr, req_queue)
        pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT)
        expiry_tenths = (
            pymqi.CMQC.MQEI_UNLIMITED if expiry is None else int(expiry * 10)
        )
        md = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT, Expiry=expiry_tenths)
        interval = 1.0 / rate
        seq = 0
        deadline = time.monotonic() + seconds
        while not STOP.is_set() and time.monotonic() < deadline:
            seq += 1
            u = uuidlib.uuid4().hex
            body = build_body(
                seq=seq, uuid=u, busdate="20260608", trade=f"TRADE-{seq}"
            )
            header = pack_header(
                password="pw", sender="FIRM01", receiver="DTCCSVC", busdate="20260608"
            ).encode()
            while True:  # ride an HA failover: retry a backed-out unit of work
                try:
                    q.put(header + body, md, pmo)
                    qmgr.commit()
                    break
                except pymqi.MQMIError as e:
                    if e.reason in _RECONNECT_BACKOUT:
                        continue
                    raise
            with lock:  # record SENT only after a clean commit
                ledger.append(LedgerEntry(Event.SENT, seq, u, time.time()))
            time.sleep(interval)
    finally:
        qmgr.disconnect()


def consumer(c, reply_queue, ledger, lock):
    qmgr = _connect(c.conn, c.channel, c.qm)
    try:
        q = pymqi.Queue(qmgr, reply_queue)
        gmo = pymqi.GMO(
            Options=pymqi.CMQC.MQGMO_SYNCPOINT
            | pymqi.CMQC.MQGMO_WAIT
            | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING,
            WaitInterval=2000,
        )
        while not STOP.is_set():
            try:
                raw = q.get(None, pymqi.MD(), gmo)
            except pymqi.MQMIError as e:
                if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                    continue
                if e.reason in _RECONNECT_BACKOUT:
                    continue  # failover; retry the get
                raise
            msg = _parse_reply(raw)
            try:
                qmgr.commit()
            except pymqi.MQMIError as e:
                if e.reason in _RECONNECT_BACKOUT:
                    continue  # get rolled back across failover; reply requeued, retry
                raise
            with lock:  # record CONFIRMED only after a clean commit
                ledger.append(
                    LedgerEntry(Event.CONFIRMED, msg.seq, msg.uuid, time.time())
                )
    finally:
        qmgr.disconnect()


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
    producer(c, args.rate, args.seconds, args.expiry, args.req_queue, ledger, lock)
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
