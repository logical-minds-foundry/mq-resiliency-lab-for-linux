"""The one MQCONNX of every mqrc client: always a CLIENT connection (#1480).

On a host with the full MQ server install (``libmqm_r.so`` beside ``libmqic_r.so``)
the MQI defaults to LOCAL bindings, even when an MQCD is supplied. There,
``MQCNO_RECONNECT`` is invalid (2046 MQRC_OPTIONS_ERROR) and, without it, the remote
queue manager's name is unknown locally (2058 MQRC_Q_MGR_NAME_ERROR). The binding the
clients used before #1470 happened to connect as a client there; ibmmq 2.1.1 does not:

- its C layer deliberately never sets ``MQCNO_CLIENT_BINDING`` (ibmmqc.c);
- its Python layer ORs ``MQCNO_CLIENT_BINDING`` into the CNO when a CD is given, but
  only AFTER it has already packed the CNO, so the bit never reaches MQCONNX
  (mqqmgr.py ``connect_with_options``);
- ``MQ_CONNECT_MODE=CLIENT`` in the environment did not force it on the lab guests.

Only an explicit ``MQCNO_CLIENT_BINDING`` in the connect options works, so every
client connects through :func:`connect_client`, which ORs it into the caller's own
options (``MQCNO_RECONNECT``, ``MQCNO_RECONNECT_Q_MGR``, ...). ``tests/test_mqconn.py``
fails if any source file in the package connects any other way.

ibmmq is imported lazily, like everywhere else in the package, so this module imports
on hosts without the MQ client libraries.
"""


def connect_client(qm, cd, sco=None, opts=0):
    """Connect to ``qm`` as an MQ client over ``cd`` (and TLS ``sco``); return the QM.

    ``opts`` are the caller's MQCNO options; ``MQCNO_CLIENT_BINDING`` is always added.
    """
    import ibmmq

    qmgr = ibmmq.QueueManager(None)
    qmgr.connect_with_options(qm, cd=cd, sco=sco, opts=opts | ibmmq.CMQC.MQCNO_CLIENT_BINDING)
    return qmgr
