"""Diagnostic: does MQCNO_RECONNECT_Q_MGR via ibmmq actually ride a QM restart?

Connect to a QM with reconnect, put under syncpoint in a loop. While it runs,
restart the QM externally. If reconnect engages, the puts pause during the
outage and resume (no fatal error). If not, we see MQRC_CONNECTION_BROKEN/2009.

    mq-reconnect-probe PCMKAPP "pcmk-vip-a.client.com(1414)" SVC.REQUEST

ibmmq is imported lazily inside probe(), so the module imports without the MQ
client libs present (unit tests, the component selfcheck).
"""

import argparse
import time

from mqrc.mqconn import connect_client

PUTS = 50
INTERVAL_S = 0.7


def probe(qm, conn, queue_name, puts=PUTS, interval=INTERVAL_S):
    """Put `puts` messages under syncpoint, reporting each outcome. A per-put MQI
    error is printed (that IS the probe's observation), not raised."""
    import ibmmq

    cd = ibmmq.CD(
        ChannelName="APP.SVRCONN",
        ConnectionName=conn,
        TransportType=ibmmq.CMQXC.MQXPT_TCP,
    )
    qmgr = connect_client(qm, cd, opts=ibmmq.CMQC.MQCNO_RECONNECT_Q_MGR)
    print("connected", flush=True)
    q = ibmmq.Queue(qmgr, queue_name)
    pmo = ibmmq.PMO(Options=ibmmq.CMQC.MQPMO_SYNCPOINT)
    md = ibmmq.MD(Persistence=ibmmq.CMQC.MQPER_PERSISTENT)
    for i in range(puts):
        try:
            q.put(b"reconnect-probe", md, pmo)
            qmgr.commit()
            print(f"put {i} ok", flush=True)
        except ibmmq.MQMIError as e:
            print(f"put {i} ERR reason={e.reason}", flush=True)
        time.sleep(interval)
    print("done", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Probe whether MQ client reconnect rides a QM restart."
    )
    ap.add_argument("qm", help="queue manager name")
    ap.add_argument("conn", help="connection name, e.g. host(1414)")
    ap.add_argument("queue", help="queue to put to")
    args = ap.parse_args(argv)
    probe(args.qm, args.conn, args.queue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
