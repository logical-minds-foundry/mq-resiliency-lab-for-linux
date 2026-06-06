"""DTCC-side responder: get TRADE.REQUEST, validate the EPN header, reply
with an ACK code via FIRM.REPLY. Client-mode via SIM.SVRCONN on localhost
(pymqi's pip build links the client library; runs on the dtcc-sim node).

Deployed to lab nodes by ansible alongside epn.py - not a dev-VM module.
"""

import datetime
import sys

import pymqi
from epn import ACK_OK, pack_header, parse_header, validate_header


def main(count: int) -> int:
    qmgr = pymqi.connect("QDTCC", "SIM.SVRCONN", "localhost(1414)")
    qin = pymqi.Queue(qmgr, "TRADE.REQUEST")
    qout = pymqi.Queue(qmgr, "FIRM.REPLY")
    today = datetime.date.today().strftime("%Y%m%d")
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=30_000)
    for _ in range(count):
        msg = qin.get(None, pymqi.MD(), gmo).decode()
        ack = validate_header(msg, today=today)
        seq = parse_header(msg).payload if ack == ACK_OK else "?"
        reply = pack_header(
            password="pw", sender="DTCCSVC", receiver="FIRM01", busdate=today
        ) + f"{ack}:{seq}"
        qout.put(reply.encode())
    qmgr.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
