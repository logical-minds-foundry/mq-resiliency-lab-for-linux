"""Unit tests for the dead-letter-queue inspector (#617 / N5).

parse_mqdlh is pure -- exercised against a synthetic MQDLH byte buffer. inspect()/
main() are driven with a fake pymqi (dlq_probe imports pymqi lazily), mirroring
tests/test_app_requester.py.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("dlq_probe", _REPO / "clients" / "dlq_probe.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dp = _load()


def _make_dlh(reason: int, dest_q: str, dest_qm: str = "PCMKAPP", *, little: bool = True) -> bytes:
    endian = "<" if little else ">"
    buf = b"DLH "
    buf += struct.pack(endian + "i", 2)  # Version
    buf += struct.pack(endian + "i", reason)  # Reason
    buf += dest_q.encode().ljust(48, b"\x00")  # DestQName
    buf += dest_qm.encode().ljust(48, b"\x00")  # DestQMgrName
    buf += b"\x00" * 64  # Encoding/CCSID/Format/PutApplType/PutApplName... (padding)
    buf += b"REPLY:original-undeliverable-message"  # the wrapped original message
    return buf


def test_parse_mqdlh_extracts_reason_and_destq_little_endian():
    dlh = dp.parse_mqdlh(_make_dlh(2051, "TESTQ"))
    assert dlh == {"reason": 2051, "dest_q": "TESTQ", "dest_qm": "PCMKAPP"}


def test_parse_mqdlh_extracts_2035_reason():
    dlh = dp.parse_mqdlh(_make_dlh(2035, "N4.TESTQ", dest_qm="PCMKAPP"))
    assert dlh["reason"] == 2035
    assert dlh["dest_q"] == "N4.TESTQ"


def test_parse_mqdlh_big_endian():
    dlh = dp.parse_mqdlh(_make_dlh(2051, "TESTQ", little=False), little_endian=False)
    assert dlh["reason"] == 2051
    assert dlh["dest_q"] == "TESTQ"


def test_parse_mqdlh_rejects_non_dlh():
    assert dp.parse_mqdlh(b"NOTADLH" + b"\x00" * 100) is None
    assert dp.parse_mqdlh(b"DLH ") is None  # too short to hold the fixed fields


def _fake_pymqi(state: dict) -> types.ModuleType:
    class MQMIError(Exception):
        def __init__(self, comp: int, reason: int) -> None:
            self.comp = comp
            self.reason = reason
            super().__init__(f"MQI Error. Comp: {comp}, Reason {reason}")

    class PYIFError(Exception):
        """pymqi's own error (best-effort teardown must swallow it too)."""

    class CMQC:
        MQCHT_CLNTCONN = 6
        MQXPT_TCP = 1
        MQOO_INPUT_AS_Q_DEF = 0x01
        MQOO_FAIL_IF_QUIESCING = 0x2000
        MQGMO_FAIL_IF_QUIESCING = 0x2000

    class _Struct:
        def __init__(self, **kw: object) -> None:
            self.Options = 0
            for key, value in kw.items():
                setattr(self, key, value)

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, _name, cd=None, sco=None, opts=0) -> None:
            pass

        def disconnect(self) -> None:
            pass

    class Queue:
        def __init__(self, _qmgr: object, _name: object, open_opts: int = 0) -> None:
            pass

        def get(self, _msg=None, _md=None, _gmo=None) -> bytes:
            if state.get("empty"):
                raise MQMIError(2, 2033)  # MQRC_NO_MSG_AVAILABLE
            return state["body"]

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
        "GMO": _Struct,
        "QueueManager": QueueManager,
        "Queue": Queue,
    }.items():
        setattr(m, name, obj)
    return m


def _args(reason: int, destq: str) -> object:
    return dp._build_args(
        [
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
            "--expect-reason",
            str(reason),
            "--expect-destq",
            destq,
        ]
    )


def test_inspect_returns_parsed_header(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"body": _make_dlh(2051, "TESTQ")}))
    dlh = dp.inspect(_args(2051, "TESTQ"))
    assert dlh["reason"] == 2051 and dlh["dest_q"] == "TESTQ"


def test_inspect_returns_none_on_empty_dlq(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"empty": True}))
    assert dp.inspect(_args(2051, "TESTQ")) is None


def test_main_pass_when_reason_and_destq_match(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"body": _make_dlh(2051, "TESTQ")}))
    assert dp.main(_argv(2051, "TESTQ")) == 0
    assert "PASS" in capsys.readouterr().out


def test_main_fail_on_reason_mismatch(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"body": _make_dlh(2035, "TESTQ")}))
    assert dp.main(_argv(2051, "TESTQ")) == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_fail_on_empty_dlq(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi({"empty": True}))
    assert dp.main(_argv(2051, "TESTQ")) == 1
    assert "FAIL" in capsys.readouterr().out


def _argv(reason: int, destq: str) -> list[str]:
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
        "--expect-reason",
        str(reason),
        "--expect-destq",
        destq,
    ]
