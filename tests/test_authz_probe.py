"""Unit tests for the induced-denial authorization probe (#630 / #617).

The pymqi MQ loop can't run here (no client libs), and authz_probe imports pymqi
lazily inside probe(), so the module imports without it and we drive probe()/main()
with a fake pymqi -- mirroring tests/test_app_requester.py. The fake records the
open options, the PMO options, and the stamped MQMD.UserIdentifier so the N4
message-context wiring can be asserted without a live queue manager.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "authz_probe", _REPO / "clients" / "authz_probe.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ap = _load()


def _fake_pymqi(state: dict) -> types.ModuleType:
    """A minimal fake `pymqi` for probe(). `state` configures where (if anywhere) an
    MQMIError is raised -- 'raise_at_connect' / 'raise_at_open' / 'raise_at_put' hold a
    reason code -- and captures the open options, PMO options and stamped UserIdentifier."""

    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class PYIFError(Exception):
        """pymqi's own error (e.g. disconnect() on a never-connected hconn)."""

    class CMQC:
        MQCHT_CLNTCONN = 6
        MQXPT_TCP = 1
        MQOO_OUTPUT = 0x10
        MQOO_FAIL_IF_QUIESCING = 0x2000
        MQOO_INPUT_AS_Q_DEF = 0x01
        MQOO_SET_IDENTITY_CONTEXT = 0x400
        MQOO_SET_ALL_CONTEXT = 0x800
        MQPMO_SET_IDENTITY_CONTEXT = 0x2000000
        MQPMO_SET_ALL_CONTEXT = 0x4000000

    class _Struct:  # stands in for CD / SCO / MD / PMO
        def __init__(self, **kw: object) -> None:
            self.Options = 0
            self.UserIdentifier = b""
            for key, value in kw.items():
                setattr(self, key, value)

    class QueueManager:
        def __init__(self, _name: object) -> None:
            self._connected = False

        def connect_with_options(self, _name, cd=None, sco=None, opts=0) -> None:
            reason = state.get("raise_at_connect")
            if reason is not None:
                raise MQMIError(2, reason)  # never becomes connected
            self._connected = True
            state["connected"] = True

        def disconnect(self) -> None:
            # Mirror pymqi: disconnect() on a never-connected hconn raises PYIFError.
            if not self._connected:
                raise PYIFError("PYMQI Error: not connected")

    class Queue:
        def __init__(self, _qmgr: object, _name: object, open_opts: int = 0) -> None:
            state["open_opts"] = open_opts
            reason = state.get("raise_at_open")
            if reason is not None:
                raise MQMIError(2, reason)

        def put(self, _msg, md=None, pmo=None) -> None:
            state["put_uid"] = getattr(md, "UserIdentifier", None)
            state["put_pmo_opts"] = getattr(pmo, "Options", None)
            reason = state.get("raise_at_put")
            if reason is not None:
                raise MQMIError(2, reason)

        def get(self, *_a, **_k) -> bytes:
            reason = state.get("raise_at_get")
            if reason is not None:
                raise MQMIError(2, reason)
            return b""

        def close(self) -> None:
            pass

    m = types.ModuleType("pymqi")
    for name, obj in {
        "MQMIError": MQMIError,
        "PYIFError": PYIFError,
        "CMQC": CMQC,
        "CD": _Struct,
        "SCO": _Struct,
        "MD": _Struct,
        "PMO": _Struct,
        "GMO": _Struct,
        "QueueManager": QueueManager,
        "Queue": Queue,
    }.items():
        setattr(m, name, obj)
    return m


def _args(argv_extra: list[str]) -> object:
    base = [
        "--qmgr",
        "PCMKAPP",
        "--conn",
        "h(1414)",
        "--channel",
        "APP.SVRCONN",
        "--cipher",
        "ANY_TLS13_OR_HIGHER",
        "--keyrepo",
        "/home/vagrant/ssl/key",
        "--queue",
        "SVC.REQUEST",
        "--op",
        "put",
        "--expect",
        "0",
    ]
    return ap._build_args(base + argv_extra)


def test_permitted_put_returns_zero(monkeypatch):
    state: dict = {}
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state))
    assert ap.probe(_args([])) == 0
    assert state["connected"] is True


def test_denied_at_connect_returns_reason(monkeypatch):
    # N2: a CHLAUTH back-stop block refuses at MQCONN -- the probe surfaces that reason.
    state = {"raise_at_connect": 2035}
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state))
    assert ap.probe(_args([])) == 2035


def test_denied_at_open_returns_reason(monkeypatch):
    # A set-context open that lacks +setall/+setid is refused at MQOPEN.
    state = {"raise_at_open": 2035}
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi(state))
    assert ap.probe(_args(["--set-context", "all", "--as-user", "mqm"])) == 2035


def test_set_context_identity_wires_open_pmo_and_uid(monkeypatch):
    state: dict = {}
    fake = _fake_pymqi(state)
    monkeypatch.setitem(sys.modules, "pymqi", fake)
    assert ap.probe(_args(["--set-context", "identity", "--as-user", "mqm"])) == 0
    assert state["open_opts"] & fake.CMQC.MQOO_SET_IDENTITY_CONTEXT
    assert state["open_opts"] & fake.CMQC.MQOO_OUTPUT  # still opened for output
    assert state["put_pmo_opts"] & fake.CMQC.MQPMO_SET_IDENTITY_CONTEXT
    assert state["put_uid"] == b"mqm"  # the spoofed identity is stamped in MQMD


def test_set_context_all_wires_set_all_context(monkeypatch):
    state: dict = {}
    fake = _fake_pymqi(state)
    monkeypatch.setitem(sys.modules, "pymqi", fake)
    assert ap.probe(_args(["--set-context", "all", "--as-user", "someone"])) == 0
    assert state["open_opts"] & fake.CMQC.MQOO_SET_ALL_CONTEXT
    assert state["put_pmo_opts"] & fake.CMQC.MQPMO_SET_ALL_CONTEXT
    assert state["put_uid"] == b"someone"


def test_no_context_leaves_flags_clean(monkeypatch):
    state: dict = {}
    fake = _fake_pymqi(state)
    monkeypatch.setitem(sys.modules, "pymqi", fake)
    ap.probe(_args([]))
    assert not (state["open_opts"] & fake.CMQC.MQOO_SET_ALL_CONTEXT)
    assert not (state["open_opts"] & fake.CMQC.MQOO_SET_IDENTITY_CONTEXT)
    assert state["put_pmo_opts"] == 0


def test_main_returns_zero_only_when_observed_matches_expect(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"raise_at_connect": 2035}))
    # observed 2035 == expect 2035 -> PASS/0
    assert ap.main(_argv_expect(2035)) == 0
    assert "PASS" in capsys.readouterr().out
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({}))
    # observed 0 != expect 2035 -> FAIL/1
    assert ap.main(_argv_expect(2035)) == 1
    assert "FAIL" in capsys.readouterr().out


def _argv_expect(expect: int) -> list[str]:
    return [
        "--qmgr",
        "PCMKAPP",
        "--conn",
        "h(1414)",
        "--channel",
        "APP.SVRCONN",
        "--cipher",
        "ANY_TLS13_OR_HIGHER",
        "--keyrepo",
        "/k",
        "--queue",
        "SVC.REQUEST",
        "--op",
        "put",
        "--expect",
        str(expect),
    ]
