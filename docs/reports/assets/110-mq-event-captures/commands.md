# Force commands — one event per class

The exact commands used to force each event on the live `NHARAPP` queue manager (run on the active node). See Report B for full per-event preconditions and cleanup.

```text
# Each block: <reason-code> <class> — the exact force command(s). Run on the active QM node.
AUTHOREV  2035 Not Authorized      : su nobody -s /bin/sh -c 'echo x | amqsput APP.REPLY NHARAPP'
LOCALEV   2085 Unknown Object Name : echo x | amqsput A.MISSING.QUEUE NHARAPP   (as mqm; queue does not exist)
INHIBTEV  2051 Put Inhibited       : ALTER QLOCAL(APP.REPLY) PUT(DISABLED); echo x | amqsput APP.REPLY NHARAPP; ALTER QLOCAL(APP.REPLY) PUT(ENABLED)
REMOTEEV  2196 Unknown Xmit Queue  : DEFINE QREMOTE(EVT.RMT) RNAME(X) RQMNAME(Y) XMITQ(NO.XMIT); echo x | amqsput EVT.RMT NHARAPP
CONFIGEV  2367/2368/2369           : DEFINE QLOCAL(EVT.CFG); ALTER QLOCAL(EVT.CFG) DESCR('event demo'); DELETE QLOCAL(EVT.CFG)
CMDEV     2412 Command MQSC        : (emitted by every mutating MQSC above; CMDEV(NODISPLAY))
PERFMEV   2224/2053 DepthHigh/Full : DEFINE QLOCAL(EVT.HI) MAXDEPTH(5) QDPHIEV(ENABLED) QDEPTHHI(80) QDPMAXEV(ENABLED); put 6 msgs
CHLEV     2283/2282 Stopped/Started: STOP CHANNEL(NHARAPP.SVCQM); START CHANNEL(NHARAPP.SVCQM)
STRSTPEV  2222 Qmgr Active         : (emitted at QM start; best-effort from journald history)
CHADEV    -- deferred (needs CHAD staging)
SSLEV     -- deferred (needs a staged TLS cert fault)

```
