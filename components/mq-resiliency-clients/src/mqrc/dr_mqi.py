"""Shared MQI reconnect contract for the DR/HA reference clients.

This module encodes, in one place, the client-side requirements for an
application to ride an IBM MQ HA failover WITHOUT loss -- derived empirically
from the lab fault drills (see docs). The future multi-language client matrix
must reproduce these same behaviours in each language/API.

The reason codes split into two families that demand different handling:

  _RETRY_INPLACE -- the connection is still usable; retry the same operation
      on the current handle.
        2003 MQRC_BACKED_OUT       the QM cleanly rolled back the uncommitted UOW
        2161 MQRC_Q_MGR_QUIESCING  a controlled endmqm is quiescing the QM
                                   (surfaced because we set FAIL_IF_QUIESCING --
                                   the cooperative option; without it a blocking
                                   MQGET-WAIT hangs through the quiesce, the
                                   classic HA lock-up)
        2549 MQRC_CALL_INTERRUPTED the connection broke DURING the commit, so the
                                   UOW outcome is UNKNOWN -- the caller must retry
                                   with the SAME business key, never blind-backout

  _RECONNECT -- the connection is GONE and MQCNO_RECONNECT did NOT transparently
      restore it. A *controlled* endmqm -w (no -r) disconnects clients
      NON-reconnectably, so a robust client cannot rely on automatic reconnect;
      it must rebuild the connection itself (new MQCONNX to the same QM via its
      connection name, which lands on the survivor once the failover completes).
      That connection name is the floating VIP for the RDQM/Pacemaker arms, or a
      multi-instance CONNAME list for the Native HA arm (#246, no VIP) — the
      client tries the list and reconnects to whichever instance is now active.
        2009 MQRC_CONNECTION_BROKEN
        2202 MQRC_CONNECTION_QUIESCING
        2059 MQRC_Q_MGR_NOT_AVAILABLE
        2018 MQRC_HCONN_ERROR

Companion infrastructure requirements (documented, not enforced here):
  * SVRCONN channel SHARECNV >= 1 (mandatory for reconnect).
  * SVRCONN channel HBINT/KAINT short (e.g. 15s) + client mqclient.ini
    TCP:KeepAlive=Yes, so a blocking MQI call to a FENCED node detects the dead
    peer in seconds rather than stalling ~HBINT (default 300s) -- otherwise a
    blocked MQPUT to a hard-fenced node hangs for minutes ("keepalive too long").
pymqi is imported lazily, inside the functions that call the MQI, so this module
(and the reason-code families below, which are the stable MQRC_* numbers rather
than pymqi.CMQC lookups) imports without the MQ client libs present -- unit tests
and the component selfcheck's module walk run on hosts with no MQ SDK.
"""

import time

# Connection still usable -- retry the same op on the current handle.
RETRY_INPLACE = (
    2003,  # MQRC_BACKED_OUT
    2161,  # MQRC_Q_MGR_QUIESCING
)
# In-doubt: commit outcome unknown. Retry with the SAME business key so a
# double-landing is a detectable duplicate, never a silent loss.
CALL_INTERRUPTED = 2549  # MQRC_CALL_INTERRUPTED
# Connection gone; auto-reconnect did not cover it (controlled endmqm). Rebuild.
RECONNECT = (
    2009,  # MQRC_CONNECTION_BROKEN
    2202,  # MQRC_CONNECTION_QUIESCING
    2059,  # MQRC_Q_MGR_NOT_AVAILABLE
    2018,  # MQRC_HCONN_ERROR
)
# MQCONNX raises this while the QM is still failing over to the survivor.
_CONNECT_RETRY = RECONNECT + (2059,)  # MQRC_Q_MGR_NOT_AVAILABLE


def connect(qm, conn, channel, keyrepo="", certlabel=""):
    """Open a reconnectable client connection to qm via conn over channel.

    TLS (#1377) mirrors mqrc.app_requester exactly: keyrepo is the keystore *stem*
    (a sibling .sth stash supplies the password -- pymqi's SCO has none) and turns
    on mutual TLS (ANY_TLS13_OR_HIGHER); certlabel picks the client cert. Omit
    keyrepo -> plaintext, as before.
    """
    import pymqi

    cd = pymqi.CD(
        ChannelName=channel.encode(),
        ConnectionName=conn.encode(),
        TransportType=pymqi.CMQC.MQXPT_TCP,
    )
    # Always a valid SCO: connect_with_options calls sco.pack(), so a None sco crashes
    # on the plaintext path. TLS just sets its fields (same as app_requester, #442).
    sco = pymqi.SCO()
    if keyrepo:
        cd.SSLCipherSpec = b"ANY_TLS13_OR_HIGHER"
        sco.KeyRepository = keyrepo.encode()
        if certlabel:
            sco.CertificateLabel = certlabel.encode()
    qmgr = pymqi.QueueManager(None)
    # MQCNO_RECONNECT_Q_MGR: auto-reconnect to the SAME QM via its conn name
    # (VIP, or the Native HA multi-instance CONNAME list). This
    # covers ABRUPT breaks (crash/kill/fence) transparently; the explicit
    # rebuild loops below cover the CONTROLLED-endmqm case it does not.
    qmgr.connect_with_options(qm, cd=cd, sco=sco, opts=pymqi.CMQC.MQCNO_RECONNECT_Q_MGR)
    return qmgr


def connect_retry(qm, conn, channel, keep_going, keyrepo="", certlabel=""):
    """MQCONNX with backoff until it succeeds or keep_going() turns False.

    MQCONNX is never auto-retried by the client library, so during a failover
    (QM not yet up on the survivor) we must retry it ourselves. Returns a live
    QueueManager, or None if keep_going() went False first. keyrepo/certlabel are
    passed straight to connect() (TLS; empty keyrepo -> plaintext).
    """
    import pymqi

    while keep_going():
        try:
            return connect(qm, conn, channel, keyrepo, certlabel)
        except pymqi.MQMIError as e:
            if e.reason in _CONNECT_RETRY:
                time.sleep(1.0)
                continue
            raise
    return None
