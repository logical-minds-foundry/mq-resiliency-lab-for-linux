"""Diagnostic: does MQCNO_RECONNECT_Q_MGR via pymqi actually ride a QM restart?

Connect to a QM with reconnect, put under syncpoint in a loop. While it runs,
restart the QM externally. If reconnect engages, the puts pause during the
outage and resume (no fatal error). If not, we see MQRC_CONNECTION_BROKEN/2009.

    ~/mqvenv/bin/python ~/reconnect_probe.py QMAIN "10.30.0.10(1414)" DTCC.REQUEST
"""

import sys
import time

import pymqi

qm, conn, queue_name = sys.argv[1], sys.argv[2], sys.argv[3]
cd = pymqi.CD(
    ChannelName=b"APP.SVRCONN",
    ConnectionName=conn.encode(),
    TransportType=pymqi.CMQC.MQXPT_TCP,
)
qmgr = pymqi.QueueManager(None)
qmgr.connect_with_options(qm, cd=cd, opts=pymqi.CMQC.MQCNO_RECONNECT_Q_MGR)
print("connected", flush=True)
q = pymqi.Queue(qmgr, queue_name)
pmo = pymqi.PMO(Options=pymqi.CMQC.MQPMO_SYNCPOINT)
md = pymqi.MD(Persistence=pymqi.CMQC.MQPER_PERSISTENT)
for i in range(50):
    try:
        q.put(b"reconnect-probe", md, pmo)
        qmgr.commit()
        print(f"put {i} ok", flush=True)
    except pymqi.MQMIError as e:
        print(f"put {i} ERR reason={e.reason}", flush=True)
    time.sleep(0.7)
print("done", flush=True)
