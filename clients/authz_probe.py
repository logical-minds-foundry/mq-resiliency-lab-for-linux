"""Induced-denial authorization probe (#74 / #630).

Connect over a SVRCONN presenting a given client certificate -- the identity a
CHLAUTH SSLPEERMAP rule assigns on the queue manager -- then attempt a single
MQPUT or MQGET on a target queue and assert the resulting MQ reason code. Exit 0
only when the observed reason matches --expect: 2035 (MQRC_NOT_AUTHORIZED) for an
induced denial, 0 (MQRC_NONE) for a permitted op. The reason code is the whole
assertion, so the harness is agnostic to whether the OAM refuses at MQOPEN or at
the MQPUT/MQGET itself.

Follows the clients/ pymqi + TLS idiom (app_requester.py / svc_responder.py): an
MQCD carrying ChannelName / ConnectionName / SSLCipherSpec, and an MQSCO carrying
the KeyRepository stem -- pymqi finds the sibling `<stem>.sth` stash for the
PKCS#12 password automatically (SCO has no password field). --certlabel is
optional: a keystore dedicated to one entity (the lab's per-entity keystores) has
a single personal certificate, so MQ selects it without a label; pass one to be
explicit or for a multi-cert store.
"""

from __future__ import annotations

import argparse

import pymqi


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Induced-denial MQ authorization probe (#630).")
    ap.add_argument("--qmgr", required=True, help="queue manager name")
    ap.add_argument("--conn", required=True, help="connection name(s), 'host(port)[,host(port)...]'")
    ap.add_argument("--channel", required=True, help="SVRCONN channel, e.g. MON.SVRCONN")
    ap.add_argument("--cipher", required=True, help="SSLCipherSpec, e.g. ANY_TLS13_OR_HIGHER")
    ap.add_argument("--keyrepo", required=True, help="keystore stem, no suffix; .sth sibling holds the password")
    ap.add_argument("--certlabel", default="", help="client cert label; omit for a single-cert keystore")
    ap.add_argument("--queue", required=True, help="target queue")
    ap.add_argument("--op", choices=["put", "get"], required=True, help="operation to attempt")
    ap.add_argument("--expect", type=int, required=True, help="expected MQ reason code (0 = permitted)")
    return ap.parse_args(argv)


def probe(args: argparse.Namespace) -> int:
    """Attempt the op as the presented identity; return the observed MQ reason code.

    0 means the op was permitted (MQRC_NONE); any other value is the reason from the
    MQMIError MQ raised -- typically 2035 (MQRC_NOT_AUTHORIZED) for an induced denial.
    Opens with only the access the op needs (MQOO_OUTPUT for put, MQOO_INPUT_AS_Q_DEF
    for get) so the assertion pins that one authority, not a superset.
    """
    cd = pymqi.CD(
        ChannelName=args.channel.encode(),
        ConnectionName=args.conn.encode(),
        ChannelType=pymqi.CMQC.MQCHT_CLNTCONN,
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    cd.SSLCipherSpec = args.cipher.encode()
    sco = pymqi.SCO(KeyRepository=args.keyrepo.encode())
    if args.certlabel:
        sco.CertificateLabel = args.certlabel.encode()

    qmgr = None
    qobj = None
    try:
        qmgr = pymqi.QueueManager(None)
        qmgr.connect_with_options(args.qmgr, cd=cd, sco=sco)
        if args.op == "put":
            qobj = pymqi.Queue(
                qmgr, args.queue, pymqi.CMQC.MQOO_OUTPUT | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING
            )
            qobj.put(b"authz-probe")
        else:
            qobj = pymqi.Queue(
                qmgr, args.queue, pymqi.CMQC.MQOO_INPUT_AS_Q_DEF | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING
            )
            qobj.get()
        observed = 0  # MQRC_NONE -- the op was permitted
    except pymqi.MQMIError as exc:
        observed = exc.reason
    finally:
        if qobj is not None:
            try:
                qobj.close()
            except pymqi.MQMIError:
                pass  # best-effort teardown -- the handle may never have opened
        if qmgr is not None:
            try:
                qmgr.disconnect()
            except pymqi.MQMIError:
                pass  # best-effort teardown -- the connection may already be gone
    return observed


def main(argv: list[str] | None = None) -> int:
    args = _build_args(argv)
    observed = probe(args)
    ok = observed == args.expect
    print(
        f"channel={args.channel} op={args.op} queue={args.queue} "
        f"observed={observed} expect={args.expect} -> {'PASS' if ok else 'FAIL'}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
