"""DTCC-side god's-eye responder (syncpoint).

Records RECEIVED for EVERY get (so a redelivered message counts as a duplicate,
spec §5) and REPLIED for every reply, into the god's-eye ledger. Runs on the
dtcc-sim node (outside both DC sites, so the oracle survives a full site loss).

Run on dtcc-sim:
    ~/mqvenv/bin/python ~/dr_responder.py --seconds 40 --ledger ~/dr-ledgers/dtcc.jsonl

Deployed by ansible alongside mqlab/. Echoes the DRv1 body back so the firm can
match seq/uuid.
"""

import argparse
import pathlib
import time

import pymqi
from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.wire import parse_body
from mqlab.epn import pack_header


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()
    pathlib.Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)

    qmgr = pymqi.connect("QDTCC", "SIM.SVRCONN", "localhost(1414)")
    qin = pymqi.Queue(qmgr, "TRADE.REQUEST")
    qout = pymqi.Queue(qmgr, "FIRM.REPLY")
    gmo = pymqi.GMO(
        Options=pymqi.CMQC.MQGMO_SYNCPOINT
        | pymqi.CMQC.MQGMO_WAIT
        | pymqi.CMQC.MQGMO_FAIL_IF_QUIESCING,
        WaitInterval=2000,
    )
    pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT)
    md_persist = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT)

    ledger = Ledger()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            raw = qin.get(None, pymqi.MD(), gmo)
        except pymqi.MQMIError as e:
            if e.reason == pymqi.CMQC.MQRC_NO_MSG_AVAILABLE:
                continue
            raise
        idx = raw.find(b"DRv1|")
        msg = parse_body(raw[idx:])
        ledger.append(LedgerEntry(Event.RECEIVED, msg.seq, msg.uuid, time.time()))
        reply = (
            pack_header(
                password="pw", sender="DTCCSVC", receiver="FIRM01", busdate=msg.busdate
            ).encode()
            + raw[idx:]  # echo the DRv1 body so the firm can match seq/uuid
        )
        qout.put(reply, md_persist, pmo)
        qmgr.commit()
        ledger.append(LedgerEntry(Event.REPLIED, msg.seq, msg.uuid, time.time()))

    ledger.write_jsonl(args.ledger)
    qmgr.disconnect()
    received = len(ledger.dtcc_receive_counts())
    print(f"responder done: {received} received -> {args.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
