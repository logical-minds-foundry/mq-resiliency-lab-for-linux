"""App requester -- Business A's application in the distributed flow (client mode).

Connects client-mode to our HA queue manager (QMPCMK) via a CONNAME list of BOTH
site VIPs, with MQCNO_RECONNECT, so it follows the QM across HA (the floating VIP
within a data centre) and DR (the other site's VIP -- tried second). Puts a
request to SVC.REQUEST -- a remote-queue definition that routes across the WAN to
QMSVC -- and gets the reply from APP.REPLY, matched by CorrelId == the request's
MsgId, the correlation contract the SVC service honours.

This is the normal-path requester. The full controlled-endmqm reconnect/rebuild
contract (in dr_mqi.py) is exercised by the DR validation (Plan 4), not here.

Deployed to the app VM by ansible. Run:
    ~/mqvenv/bin/python ~/app_requester.py --count 5
"""

import argparse
import os
import time

import pymqi

# Both site VIPs: site-A floats within DC-A (HA); site-B is the DR endpoint. The
# client probes A then B, so it rides HA transparently and follows a DR cutover.
DEFAULT_CONN = "10.10.1.200(1414),10.10.2.200(1414)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qm", default=os.environ.get("MQLAB_QM", "QMPCMK"))
    ap.add_argument("--conn", default=DEFAULT_CONN)
    ap.add_argument("--channel", default="APP.SVRCONN")
    ap.add_argument("--request-queue", default="SVC.REQUEST")
    ap.add_argument("--reply-queue", default="APP.REPLY")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.0)
    # TLS (#250): --keyrepo is the keystore *stem* (no .p12); a sibling .sth stash
    # supplies the password (pymqi's SCO has none). Omit both -> plaintext.
    ap.add_argument("--keyrepo", default="", help="keystore stem, e.g. /home/vagrant/ssl/app-client")
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    args = ap.parse_args()

    cd = pymqi.CD(
        ChannelName=args.channel.encode(),
        ConnectionName=args.conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    sco = None
    if args.keyrepo:
        # Mutual TLS 1.3 to the SVRCONN. The .sth next to <keyrepo>.p12 is read
        # automatically; CertificateLabel selects which cert we present.
        cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
        sco = pymqi.SCO(KeyRepository=args.keyrepo.encode())
        if args.certlabel:
            sco.CertificateLabel = args.certlabel.encode()
    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options(args.qm, cd=cd, sco=sco, opts=pymqi.CMQC.MQCNO_RECONNECT)

    qreq = pymqi.Queue(qmgr, args.request_queue)
    qrep = pymqi.Queue(qmgr, args.reply_queue)
    gmo = pymqi.GMO(
        Options=(pymqi.CMQC.MQGMO_WAIT | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING),
        WaitInterval=15000,
        MatchOptions=pymqi.CMQC.MQMO_MATCH_CORREL_ID,
    )
    ok = 0
    try:
        for i in range(args.count):
            put_md = pymqi.MD(
                ReplyToQ=args.reply_queue.encode(),
                ReplyToQMgr=args.qm.encode(),
                Format=pymqi.CMQC.MQFMT_STRING,
                Persistence=pymqi.CMQC.MQPER_PERSISTENT,
            )
            t0 = time.monotonic()
            qreq.put(f"req-{i:04d}".encode(), put_md)  # MsgId is set on the MD here
            get_md = pymqi.MD(CorrelId=put_md.MsgId)  # match the reply to our request
            reply = qrep.get(None, get_md, gmo)
            dt = (time.monotonic() - t0) * 1000
            print(f"[{i}] round-trip {dt:.0f}ms <- {reply.decode(errors='replace')[:48]}")
            ok += 1
            time.sleep(args.interval)
    finally:
        qmgr.disconnect()
    print(f"{ok}/{args.count} round-trips OK")
    return 0 if ok == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
