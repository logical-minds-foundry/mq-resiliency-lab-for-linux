"""Every client connect forces client mode with MQCNO_CLIENT_BINDING (#1480).

On guests with the full MQ server install, ibmmq connects in LOCAL bindings unless
MQCNO_CLIENT_BINDING is in the connect options: MQCNO_RECONNECT then fails with 2046
and a plain connect with 2058. Two guards:

- behaviour: mqrc.mqconn.connect_client ORs MQCNO_CLIENT_BINDING into whatever options
  the caller passes (against a fake ibmmq that records the call);
- structure: the package's own source (mqrc.ibmmq_refs.connect_calls, ``ast``) has
  exactly one queue-manager connect, in mqrc/mqconn.py, and it names
  MQCNO_CLIENT_BINDING in ``opts``. Any other connect -- a new client calling
  ``connect_with_options`` directly, ``ibmmq.connect``, ``QueueManager(name)`` --
  fails this test.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from mqrc import ibmmq_refs, mqconn

CLIENT_BINDING = 0x800  # MQCNO_CLIENT_BINDING (ibmmq CMQC, every platform)
RECONNECT = 0x01000000  # MQCNO_RECONNECT
RECONNECT_Q_MGR = 0x04000000  # MQCNO_RECONNECT_Q_MGR


def _fake_ibmmq(calls: list) -> types.ModuleType:
    class CMQC:
        MQCNO_CLIENT_BINDING = CLIENT_BINDING

    class QueueManager:
        def __init__(self, name: object) -> None:
            assert name is None, "QueueManager(<name>) connects at construction"

        def connect_with_options(self, qm, cd=None, sco=None, opts=0) -> None:
            calls.append((qm, cd, sco, opts))

    mod = types.ModuleType("ibmmq")
    mod.CMQC = CMQC
    mod.QueueManager = QueueManager
    return mod


@pytest.mark.parametrize(
    ("opts", "expected"),
    [
        (0, CLIENT_BINDING),
        (RECONNECT, RECONNECT | CLIENT_BINDING),
        (RECONNECT_Q_MGR, RECONNECT_Q_MGR | CLIENT_BINDING),
        (CLIENT_BINDING, CLIENT_BINDING),
    ],
)
def test_connect_client_always_adds_client_binding(monkeypatch, opts, expected):
    calls: list = []
    monkeypatch.setitem(sys.modules, "ibmmq", _fake_ibmmq(calls))
    cd, sco = object(), object()
    qmgr = mqconn.connect_client("QM1", cd, sco, opts=opts)
    assert calls == [("QM1", cd, sco, expected)]
    assert qmgr is not None


def test_connect_client_defaults_no_sco_and_no_caller_options(monkeypatch):
    calls: list = []
    monkeypatch.setitem(sys.modules, "ibmmq", _fake_ibmmq(calls))
    cd = object()
    mqconn.connect_client("QM1", cd)
    assert calls == [("QM1", cd, None, CLIENT_BINDING)]


# --- structure: the package connects only through connect_client ---------------------


def test_the_only_connect_in_the_package_is_connect_client_and_it_forces_client_mode():
    calls = ibmmq_refs.connect_calls(ibmmq_refs.source_files())
    assert [(c.where.split(":")[0], c.how, c.client_binding) for c in calls] == [
        ("mqconn.py", "connect_with_options", True)
    ], "connect through mqrc.mqconn.connect_client (MQCNO_CLIENT_BINDING, #1480):\n" + "\n".join(
        map(str, calls)
    )


def test_every_client_module_connects_through_connect_client():
    # The clients that connect (issue #1480's list); each must reference the helper.
    pkg = ibmmq_refs.package_dir()
    for name in (
        "app_requester",
        "authz_probe",
        "bench_client",
        "dlq_probe",
        "dr_mqi",
        "reconnect_probe",
        "svc_responder",
    ):
        assert "connect_client(" in (pkg / f"{name}.py").read_text(), name


def _calls(tmp_path: Path, text: str) -> list[tuple[str, str, bool]]:
    (tmp_path / "mod.py").write_text(text)
    found = ibmmq_refs.connect_calls(ibmmq_refs.source_files(tmp_path), tmp_path)
    return [(c.where, c.how, c.client_binding) for c in found]


def test_connect_calls_flags_every_way_to_connect(tmp_path):
    assert _calls(
        tmp_path,
        "def f(ibmmq, q, cd, o):\n"
        "    q.connect_with_options('QM', cd=cd, opts=o | ibmmq.CMQC.MQCNO_CLIENT_BINDING)\n"
        "    q.connect_with_options('QM', cd=cd, opts=MQCNO_CLIENT_BINDING)\n"
        "    q.connect_with_options('QM', cd=cd, opts=ibmmq.CMQC.MQCNO_RECONNECT)\n"
        "    q.connect_with_options('QM', cd=cd)\n"
        "    q.connect_tcp_client('QM', cd, 'CH', 'h(1)', opts=MQCNO_CLIENT_BINDING)\n"
        "    ibmmq.connect('QM', 'CH', 'h(1)')\n"
        "    q.connect('QM')\n"
        "    ibmmq.QueueManager('QM')\n"
        "    ibmmq.QueueManager()\n"
        "    ibmmq.QueueManager(name='QM')\n"
        "    ibmmq.QueueManager(None)\n"
        "    ibmmq.QueueManager(name=None)\n"
        "    q.disconnect()\n"
        "    other.QueueManager('QM')\n",
    ) == [
        ("mod.py:10", "QueueManager", False),
        ("mod.py:11", "QueueManager", False),
        ("mod.py:2", "connect_with_options", True),
        ("mod.py:3", "connect_with_options", True),
        ("mod.py:4", "connect_with_options", False),
        ("mod.py:5", "connect_with_options", False),
        ("mod.py:6", "connect_tcp_client", False),
        ("mod.py:7", "connect", False),
        ("mod.py:8", "connect", False),
        ("mod.py:9", "QueueManager", False),
    ]


def test_connect_calls_rejects_the_pre_1480_connect(tmp_path):
    # The exact shape every client used before #1480 is caught.
    assert _calls(
        tmp_path,
        "def f(ibmmq, args, cd, sco):\n"
        "    qmgr = ibmmq.QueueManager(None)\n"
        "    qmgr.connect_with_options(args.qm, cd=cd, sco=sco, opts=ibmmq.CMQC.MQCNO_RECONNECT)\n",
    ) == [("mod.py:3", "connect_with_options", False)]
