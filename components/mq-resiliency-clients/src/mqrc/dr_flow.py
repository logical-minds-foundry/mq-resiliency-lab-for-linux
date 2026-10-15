"""App-side continuous flow generator (persistent + syncpoint, HA-aware).

Producer: build_body() -> MQPUT (PERSISTENT, FAIL_IF_QUIESCING) under syncpoint
          -> commit -> app ledger SENT.
Consumer: MQGET reply (FAIL_IF_QUIESCING) under syncpoint -> commit -> app
          ledger CONFIRMED.

Only its OWN replies (#1550): the reply queue may be shared (APP.REPLY also carries
mq-app-requester's replies), so each run picks one 24-byte run CorrelId, the producer
puts every request with it, mq-dr-responder copies it onto the reply, and the consumer
GETs with MQMO_MATCH_CORREL_ID on it -- it never takes another client's message. A
correlated reply with no DRv1 body is a real fault, not noise.

Fail loud (#1550): an unexpected exception in the producer or the consumer stops the
run (STOP) and makes main() print the cause and return 1 -- confirmation tracking never
dies silently in a thread while the run reports success.

Producer and consumer run on SEPARATE MQ connections (a single Hconn is not safe
to share across threads -- MQRC_HCONN_ERROR); the ledger is shared under a lock.

Each side wraps its work in an explicit reconnect loop (see dr_mqi.py): it
cooperates with a controlled endmqm via FAIL_IF_QUIESCING, retries in-doubt
operations against the same business key, and rebuilds the connection itself
when a controlled endmqm -w disconnects it non-reconnectably (auto-reconnect
only covers abrupt breaks). The producer carries the in-flight message's
seq/uuid across a rebuild so a failover never drops or silently re-keys it.

Target QM / connection / queues are parameterized so the same client drives any
arm; it defaults to the Pacemaker arm (PCMKAPP) via its VIP FQDNs, e.g.
    mq-dr-flow --qm PCMKAPP \
        --conn "pcmk-vip-a.client.com(1414),pcmk-vip-b.client.com(1414)" \
        --req-queue DR.REQUEST --reply-queue DR.REPLY \
        --rate 20 --seconds 30 --ledger ~/dr-ledgers/app.jsonl

Installed as the `mq-dr-flow` console script of the mq-resiliency-clients component.
ibmmq is imported lazily inside the MQI functions, so the module imports without
the MQ client libs present (unit tests, the component selfcheck).
"""

import argparse
import os
import pathlib
import sys
import threading
import time
import traceback
import uuid as uuidlib

from mqrc import dr_mqi
from mqrc.dr.ledger import Event, Ledger, LedgerEntry
from mqrc.dr.wire import NotDRv1Error, build_body, split_framed
from mqrc.header import pack_header

STOP = threading.Event()
DRAIN_SECONDS = 4.0
CORREL_ID_LEN = 24  # MQBYTE24


def new_run_correl_id():
    """A CorrelId unique to this run: a uuid4's 16 bytes, NUL-padded to MQ's 24."""
    return uuidlib.uuid4().bytes.ljust(CORREL_ID_LEN, b"\0")


def _parse_reply(raw):
    """The reply echoes the DRv1 body after the FFH header. Raises NotDRv1Error when
    there is no DRv1 body -- never parses a slice from find() == -1."""
    return split_framed(raw)[1]


def producer(c, rate, seconds, expiry, req_queue, correl_id, ledger, lock, ledger_path):
    import ibmmq

    deadline = time.monotonic() + seconds
    keep = lambda: not STOP.is_set() and time.monotonic() < deadline
    last_flush = time.monotonic()
    # NEW_MSG_ID: the QM generates a unique MsgId for every put, even if a
    # descriptor were ever reused (#1549).
    pmo = ibmmq.PMO(
        Options=(
            ibmmq.CMQC.MQPMO_SYNCPOINT
            | ibmmq.CMQC.MQPMO_NEW_MSG_ID
            | ibmmq.CMQC.MQPMO_FAIL_IF_QUIESCING
        )
    )
    expiry_tenths = ibmmq.CMQC.MQEI_UNLIMITED if expiry is None else int(expiry * 10)
    interval = 1.0 / rate

    qmgr, q, seq = None, None, 0
    pending = None  # (seq, uuid, payload) built but not yet confirmed-sent
    while keep():
        if qmgr is None:
            qmgr = c.connect_retry(keep)
            if qmgr is None:
                break
            q = ibmmq.Queue(qmgr, req_queue)
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
            # A fresh MD per put: MQPUT writes the generated MsgId back into the
            # descriptor, so a reused MD would resend it on every later put (#1549).
            # CorrelId = the run id, which the responder echoes so the consumer can
            # take only this run's replies (#1550).
            md = ibmmq.MD(
                Persistence=ibmmq.CMQC.MQPER_PERSISTENT,
                Expiry=expiry_tenths,
                CorrelId=correl_id,
            )
            q.put(payload, md, pmo)
            qmgr.commit()
        except ibmmq.MQMIError as e:
            if e.reason in dr_mqi.RECONNECT:
                # Controlled endmqm disconnected us non-reconnectably: drop the
                # dead connection and rebuild. `pending` is kept so the SAME
                # seq/uuid is resent (the broken commit did not land) -- no loss.
                try:
                    qmgr.disconnect()
                except ibmmq.MQMIError:
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
                except ibmmq.MQMIError:
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
        except ibmmq.MQMIError:
            pass


def consumer(c, reply_queue, correl_id, ledger, lock):
    import ibmmq

    keep = lambda: not STOP.is_set()
    # FAIL_IF_QUIESCING: the blocking MQGET-WAIT must notice a controlled endmqm
    # quiesce instead of hanging through it.
    gmo = ibmmq.GMO(
        Options=(
            ibmmq.CMQC.MQGMO_SYNCPOINT | ibmmq.CMQC.MQGMO_WAIT | ibmmq.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=2000,
        MatchOptions=ibmmq.CMQC.MQMO_MATCH_CORREL_ID,  # this run's replies only
    )
    qmgr, q = None, None
    while keep():
        if qmgr is None:
            qmgr = c.connect_retry(keep)
            if qmgr is None:
                break
            q = ibmmq.Queue(qmgr, reply_queue)
        try:
            # Fresh MD per get: a GET fills the MD, and a reused one would then match
            # only that message's ids.
            raw = q.get(None, ibmmq.MD(CorrelId=correl_id), gmo)
        except ibmmq.MQMIError as e:
            if e.reason == ibmmq.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            if e.reason in dr_mqi.RECONNECT:
                try:
                    qmgr.disconnect()
                except ibmmq.MQMIError:
                    pass
                qmgr, q = None, None
                time.sleep(0.5)
                continue
            if e.reason in dr_mqi.RETRY_INPLACE or e.reason == dr_mqi.CALL_INTERRUPTED:
                time.sleep(0.2)  # yield to the reconnect thread; no busy-spin
                continue
            raise
        try:
            msg = _parse_reply(raw)
        except NotDRv1Error:
            # A reply correlated to this run but with no DRv1 body is a real fault.
            # Back it out (it stays on the queue for inspection) and fail loud.
            qmgr.backout()
            raise
        try:
            qmgr.commit()
        except ibmmq.MQMIError as e:
            if e.reason in dr_mqi.RECONNECT:
                try:
                    qmgr.disconnect()
                except ibmmq.MQMIError:
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
        except ibmmq.MQMIError:
            pass


class _Conn:
    def __init__(self, conn, channel, qm, keyrepo="", certlabel=""):
        self.conn, self.channel, self.qm = conn, channel, qm
        self.keyrepo, self.certlabel = keyrepo, certlabel

    def connect_retry(self, keep_going):
        return dr_mqi.connect_retry(
            self.qm,
            self.conn,
            self.channel,
            keep_going,
            keyrepo=self.keyrepo,
            certlabel=self.certlabel,
        )


def _guarded(name, fn, errors, *args):
    """Run one side of the flow. An unexpected exception is printed, recorded in
    `errors` and stops the other side (STOP), so main() can fail loud."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 -- recorded, then main() reports it and exits 1
        print(f"mq-dr-flow: {name} failed:", file=sys.stderr, flush=True)
        traceback.print_exception(exc, file=sys.stderr)
        errors.append((name, exc))
        STOP.set()


def main(argv=None):
    ap = argparse.ArgumentParser()
    # Defaults target the Pacemaker arm's app QM via its generated VIP FQDNs (#495);
    # the legacy QMAIN @ 10.30.0.10 single-QM default predated the #351 four-stack model.
    ap.add_argument("--qm", default=os.environ.get("MQLAB_QM", "PCMKAPP"))
    ap.add_argument("--conn", default="pcmk-vip-a.client.com(1414),pcmk-vip-b.client.com(1414)")
    ap.add_argument("--channel", default="APP.SVRCONN")
    ap.add_argument("--req-queue", default="SVC.REQUEST")
    ap.add_argument("--reply-queue", default="APP.REPLY")
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument(
        "--expiry",
        type=float,
        default=None,
        help="per-message expiry in seconds (default: unlimited)",
    )
    ap.add_argument("--ledger", required=True)
    # TLS (#1377, mirrors app_requester #250): --keyrepo is the keystore *stem*; a
    # sibling .sth supplies the password (the clients use the .sth stash;
    # SCO.KeyRepoPassword adoption is deferred, epic .github#300 §9). Omit both -> plaintext.
    ap.add_argument("--keyrepo", default="", help="keystore stem, e.g. /home/vagrant/ssl/key")
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    args = ap.parse_args(argv)
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    STOP.clear()  # a fresh run (STOP is module-global)
    c = _Conn(args.conn, args.channel, args.qm, args.keyrepo, args.certlabel)
    ledger, lock = Ledger(), threading.Lock()
    correl_id = new_run_correl_id()
    errors = []
    t = threading.Thread(
        target=_guarded,
        args=("consumer", consumer, errors, c, args.reply_queue, correl_id, ledger, lock),
        daemon=True,
    )
    t.start()
    _guarded(
        "producer",
        producer,
        errors,
        c,
        args.rate,
        args.seconds,
        args.expiry,
        args.req_queue,
        correl_id,
        ledger,
        lock,
        args.ledger,
    )
    if not errors:
        time.sleep(DRAIN_SECONDS)  # let in-flight replies land before stopping
    STOP.set()
    t.join(timeout=5)
    with lock:
        ledger.write_jsonl(args.ledger)
    sent = len(ledger.sent_seqs())
    confirmed = len(ledger.confirmed_seqs())
    print(f"flow done: {sent} sent, {confirmed} confirmed -> {args.ledger}")
    if errors:
        failed = ", ".join(f"{name} ({exc!r})" for name, exc in errors)
        print(f"mq-dr-flow: FAILED -- {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
