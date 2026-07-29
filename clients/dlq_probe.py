"""Dead-letter queue inspector (#617 / N5).

GET one message from a queue (typically SYSTEM.DEAD.LETTER.QUEUE) over a client
connection, parse its MQDLH dead-letter header, and assert the Reason code and
DestQName. Used to prove that an induced-undeliverable message landed on the queue
manager's single DLQ with the expected reason (2051 MQRC_PUT_INHIBITED for N5;
2035 MQRC_NOT_AUTHORIZED for the N4 +setall-load-bearing check) and the intended
destination queue -- and, because the GET removes the message, to drain the test
message afterwards so the shared arm is left clean.

Follows the clients/ pymqi + TLS idiom (authz_probe.py): an MQCD with
ChannelName/ConnectionName/SSLCipherSpec and an MQSCO with the KeyRepository stem
(the sibling `.sth` supplies the PKCS#12 password). pymqi is imported lazily inside
inspect() so the module (and the pure parse_mqdlh helper) imports without the MQ
client libs present -- unit tests drive it with a fake pymqi and a synthetic MQDLH.

MQDLH layout (IBM MQ, cmqc.h), numeric fields in the message's own encoding:

    off  size  field
      0     4  StrucId        "DLH "
      4     4  Version
      8     4  Reason         <-- MQLONG reason code
     12    48  DestQName      <-- the queue the message could not be put to
     60    48  DestQMgrName
    108     4  Encoding
    ...
"""

from __future__ import annotations

import argparse
import struct

_MQRC_NO_MSG_AVAILABLE = 2033
_DLH_STRUC_ID = b"DLH "


def parse_mqdlh(body: bytes, *, little_endian: bool = True) -> dict | None:
    """Parse the leading MQDLH from a dead-letter message body.

    Returns {'reason', 'dest_q', 'dest_qm'} or None when `body` is not an MQDLH
    (no "DLH " eyecatcher, or too short). The QM writes the numeric fields in its
    native integer encoding; on the lab's x86-64 Linux QMs that is little-endian,
    the default -- pass little_endian=False for a big-endian (MQENC_INTEGER_NORMAL)
    message.
    """
    if len(body) < 60 or body[0:4] != _DLH_STRUC_ID:
        return None
    endian = "<" if little_endian else ">"
    (reason,) = struct.unpack(endian + "i", body[8:12])
    dest_q = body[12:60].rstrip(b" \x00").decode(errors="replace")
    dest_qm = body[60:108].rstrip(b" \x00").decode(errors="replace") if len(body) >= 108 else ""
    return {"reason": reason, "dest_q": dest_q, "dest_qm": dest_qm}


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Dead-letter queue inspector (#617 / N5).")
    ap.add_argument("--qmgr", required=True, help="queue manager name")
    ap.add_argument(
        "--conn", required=True, help="connection name(s), 'host(port)[,host(port)...]'"
    )
    ap.add_argument("--channel", required=True, help="SVRCONN channel")
    ap.add_argument("--cipher", required=True, help="SSLCipherSpec, e.g. ANY_TLS13_OR_HIGHER")
    ap.add_argument(
        "--keyrepo", required=True, help="keystore stem; .sth sibling holds the password"
    )
    ap.add_argument(
        "--certlabel", default="", help="client cert label; omit for a single-cert keystore"
    )
    ap.add_argument("--queue", default="SYSTEM.DEAD.LETTER.QUEUE", help="DLQ to inspect")
    ap.add_argument(
        "--expect-reason", type=int, required=True, help="expected MQDLH.Reason (e.g. 2051)"
    )
    ap.add_argument("--expect-destq", required=True, help="expected MQDLH.DestQName")
    ap.add_argument(
        "--big-endian",
        action="store_true",
        help="parse MQDLH numeric fields big-endian (default: native little-endian)",
    )
    return ap.parse_args(argv)


def inspect(args: argparse.Namespace) -> dict | None:
    """GET (and thereby drain) one message from the DLQ and parse its MQDLH.

    Returns the parsed header dict, or None when the DLQ was empty / the message
    carried no MQDLH. The GET is destructive by design -- inspecting the induced
    dead-letter also cleans it up.
    """
    import pymqi

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
        qobj = pymqi.Queue(
            qmgr, args.queue, pymqi.CMQC.MQOO_INPUT_AS_Q_DEF | pymqi.CMQC.MQOO_FAIL_IF_QUIESCING
        )
        md = pymqi.MD()
        gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING, WaitInterval=0)
        try:
            body = qobj.get(None, md, gmo)
        except pymqi.MQMIError as exc:
            if exc.reason == _MQRC_NO_MSG_AVAILABLE:
                return None  # DLQ empty -- nothing dead-lettered
            raise  # never swallow an unexpected MQI error
        return parse_mqdlh(body, little_endian=not args.big_endian)
    finally:
        # Best-effort teardown -- swallow both MQMIError and pymqi's PYIFError
        # ('not connected') so a failed connect/open never masks the result.
        if qobj is not None:
            try:
                qobj.close()
            except (pymqi.MQMIError, pymqi.PYIFError):
                pass
        if qmgr is not None:
            try:
                qmgr.disconnect()
            except (pymqi.MQMIError, pymqi.PYIFError):
                pass


def main(argv: list[str] | None = None) -> int:
    args = _build_args(argv)
    dlh = inspect(args)
    if dlh is None:
        print(f"queue={args.queue} -> FAIL (no dead-letter message / no MQDLH found)", flush=True)
        return 1
    ok = dlh["reason"] == args.expect_reason and dlh["dest_q"] == args.expect_destq
    print(
        f"queue={args.queue} dlh.reason={dlh['reason']} dlh.destq={dlh['dest_q']} "
        f"dlh.destqm={dlh['dest_qm']} expect-reason={args.expect_reason} "
        f"expect-destq={args.expect_destq} -> {'PASS' if ok else 'FAIL'}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
