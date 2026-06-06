"""Firm-side requester: put N trades to DTCC.REQUEST on QMAIN over a client
connection (APP.SVRCONN), then get N ACKs from TRADE.REPLY.

Deployed to lab nodes by ansible alongside epn.py - not a dev-VM module.
"""

import datetime
import sys

import pymqi
from epn import pack_header, parse_header


def main(count: int) -> int:
    today = datetime.date.today().strftime("%Y%m%d")
    cd = pymqi.CD(
        ChannelName=b"APP.SVRCONN",
        ConnectionName=b"10.30.0.10(1414)",
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    qmgr = pymqi.QueueManager(None)
    qmgr.connect_with_options("QMAIN", cd=cd)
    qreq = pymqi.Queue(qmgr, "DTCC.REQUEST")
    qrep = pymqi.Queue(qmgr, "TRADE.REPLY")
    for i in range(count):
        body = pack_header(
            password="pw", sender="FIRM01", receiver="DTCCSVC", busdate=today
        ) + f"TRADE-{i:04d}"
        qreq.put(body.encode())
    gmo = pymqi.GMO(Options=pymqi.CMQC.MQGMO_WAIT, WaitInterval=60_000)
    acks = 0
    for _ in range(count):
        reply = qrep.get(None, pymqi.MD(), gmo).decode()
        code, seq = parse_header(reply).payload.split(":", 1)
        print(f"ack {seq}: {code}")
        if code == "0000":
            acks += 1
    qmgr.disconnect()
    print(f"{acks}/{count} clean ACKs")
    return 0 if acks == count else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
