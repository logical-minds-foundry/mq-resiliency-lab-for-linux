"""SVC service responder -- the counterparty (Business B) side of the QM-to-QM flow.

Runs co-located with the SVC QM on the SVC service VM in BINDINGS mode (a local
connection, no client channel). Reads each request from SVC.REQUEST, sends a reply
to the request's ReplyToQ / ReplyToQMgr, and honours the request/reply correlation
contract so the requesting app can match it:

  * reply CorrelId = request MsgId   (the match key the app GETs by)
  * MQPMO_NEW_MSG_ID                 (the reply gets its own MsgId)

Get + put run under syncpoint with a single commit, so a crash never loses a
request or emits a duplicate reply. MQI errors are never swallowed -- the process
exits non-zero (fail loud) and systemd restarts it.

Deployed and run as a systemd service by the mq-inter-qm role (#147).
"""

from __future__ import annotations

import argparse

import pymqi


def serve(
    qmgr_name: str,
    in_queue: str,
    channel: str,
    conn: str,
    keyrepo: str = "",
    certlabel: str = "",
) -> None:
    """Get/reply/commit forever on a client connection to `qmgr_name`.

    Client-mode to localhost -- the QM is co-located, but pip-installed pymqi
    links the client library only, so a true bindings connect fails 2058 (#180).
    A loopback client connection is local-in-spirit and matches dr_responder.py.

    TLS (#250): when `keyrepo` is given (the keystore stem, no .p12), connect with
    mutual TLS 1.3 -- a sibling .sth stash supplies the PKCS#12 password and
    `certlabel` selects the cert. Omit it for a plaintext connect.
    """
    cd = pymqi.CD(
        ChannelName=channel.encode(),
        ConnectionName=conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    sco = None
    if keyrepo:
        cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
        sco = pymqi.SCO(KeyRepository=keyrepo.encode())
        if certlabel:
            sco.CertificateLabel = certlabel.encode()
    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options(qmgr_name, cd=cd, sco=sco)
    qin = pymqi.Queue(qmgr, in_queue)
    gmo = pymqi.GMO(
        Options=(
            pymqi.CMQC.MQGMO_SYNCPOINT
            | pymqi.CMQC.MQGMO_WAIT
            | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING
        ),
        WaitInterval=5000,
    )
    pmo = pymqi.PMO(
        Options=(
            pymqi.CMQC.MQPMO_SYNCPOINT
            | pymqi.CMQC.MQPMO_NEW_MSG_ID
            | pymqi.CMQC.MQPMO_FAIL_IF_QUIESCING
        )
    )
    try:
        while True:
            req_md = pymqi.MD()
            try:
                body = qin.get(None, req_md, gmo)
            except pymqi.MQMIError as exc:
                if exc.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                    continue
                raise  # fail loud -- never swallow an MQI error
            reply_md = pymqi.MD(
                CorrelId=req_md.MsgId,  # the correlation contract
                Persistence=pymqi.CMQC.MQPER_PERSISTENT,
                Format=req_md.Format,
            )
            reply_od = pymqi.OD(
                ObjectName=req_md.ReplyToQ,
                ObjectQMgrName=req_md.ReplyToQMgr,  # resolves to the xmitq -> SENDER
            )
            qout = pymqi.Queue(qmgr)
            qout.open(reply_od, pymqi.CMQC.MQOO_OUTPUT | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING)
            qout.put(b"REPLY:" + body, reply_md, pmo)
            qout.close()
            qmgr.commit()
    finally:
        qmgr.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--qm", default="PCMKSVC")
    ap.add_argument("--in-queue", default="SVC.REQUEST")
    ap.add_argument("--channel", default="SVC.SVRCONN")
    ap.add_argument("--conn", default="localhost(1414)")
    # TLS (#250): keystore stem + cert label; omit both for a plaintext connect.
    ap.add_argument("--keyrepo", default="", help="keystore stem, e.g. /var/mqm/ssl/svc-responder/key")
    ap.add_argument("--certlabel", default="", help="client cert label (the entity CN)")
    args = ap.parse_args()
    serve(args.qm, args.in_queue, args.channel, args.conn, args.keyrepo, args.certlabel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
