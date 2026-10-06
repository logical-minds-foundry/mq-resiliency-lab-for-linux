"""The DR entry points' argv-based main()s and dr_mqi's lazy pymqi (epic .github#294 T8).

dr_baseline / dr_forced took positional Python arguments as loose scripts; as console
scripts they parse argv. dr_mqi's reason-code families are literal MQRC_* numbers so
the module imports without pymqi; connect_retry is driven with a fake pymqi.
"""

from __future__ import annotations

import sys
import types

import pytest

from mqrc import dr_baseline, dr_forced, dr_mqi
from mqrc.dr import Ledger, LedgerEntry
from mqrc.dr.ledger import Event
from mqrc.dr.report import SelfCorrectnessError


def _ledgers(tmp_path, confirmed: set[int], sent_ts: dict[int, float]):
    app, svc = Ledger(), Ledger()
    for seq, ts in sent_ts.items():
        app.append(LedgerEntry(Event.SENT, seq, f"u{seq}", ts))
        if seq in confirmed:
            app.append(LedgerEntry(Event.CONFIRMED, seq, f"u{seq}", ts + 0.1))
            svc.append(LedgerEntry(Event.RECEIVED, seq, f"u{seq}", ts + 0.05))
            svc.append(LedgerEntry(Event.REPLIED, seq, f"u{seq}", ts + 0.06))
    app_path, svc_path = tmp_path / "app.jsonl", tmp_path / "svc.jsonl"
    app.write_jsonl(app_path)
    svc.write_jsonl(svc_path)
    return str(app_path), str(svc_path)


def test_baseline_passes_a_self_correct_run(tmp_path, capsys):
    app, svc = _ledgers(tmp_path, {1, 2, 3}, {1: 10.0, 2: 11.0, 3: 12.0})
    assert dr_baseline.main([app, svc]) == 0
    assert "SELF-CORRECT OK: 3 messages" in capsys.readouterr().out


def test_baseline_fails_loud_when_a_message_is_unconfirmed(tmp_path):
    app, svc = _ledgers(tmp_path, {1, 2}, {1: 10.0, 2: 11.0, 3: 12.0})
    with pytest.raises(SelfCorrectnessError, match="seq 3"):
        dr_baseline.main([app, svc])


def test_baseline_requires_both_ledgers():
    with pytest.raises(SystemExit) as exc:
        dr_baseline.main(["only-one.jsonl"])
    assert exc.value.code == 2


def test_forced_attributes_the_post_break_window_as_lost(tmp_path, capsys):
    app, svc = _ledgers(tmp_path, {1}, {1: 10.0, 2: 20.0, 3: 25.0, 4: 40.0})
    assert dr_forced.main([app, svc, "15", "30"]) == 0
    out = capsys.readouterr().out
    assert "FORCED-DR: 4 sent, 2 committed at A after the replication break" in out
    assert "window [15,30]" in out


def _fake_pymqi(failures: list[int]) -> types.ModuleType:
    class MQMIError(Exception):
        def __init__(self, reason: int) -> None:
            self.reason = reason

    class CMQC:
        MQXPT_TCP = 1
        MQCNO_RECONNECT_Q_MGR = 0x4000000

    class QueueManager:
        def __init__(self, _name: object) -> None:
            pass

        def connect_with_options(self, qm: str, cd: object, opts: int) -> None:
            if failures:
                raise MQMIError(failures.pop(0))
            self.qm, self.cd, self.opts = qm, cd, opts

    mod = types.ModuleType("pymqi")
    mod.MQMIError = MQMIError
    mod.CMQC = CMQC
    mod.CD = lambda **kw: types.SimpleNamespace(**kw)
    mod.QueueManager = QueueManager
    return mod


def test_reason_code_families_are_the_mqrc_numbers():
    assert dr_mqi.RETRY_INPLACE == (2003, 2161)
    assert dr_mqi.CALL_INTERRUPTED == 2549
    assert dr_mqi.RECONNECT == (2009, 2202, 2059, 2018)


def test_connect_retry_rides_out_a_failover(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi([2059, 2009]))
    monkeypatch.setattr(dr_mqi.time, "sleep", lambda _s: None)
    qmgr = dr_mqi.connect_retry("PCMKAPP", "vip(1414)", "APP.SVRCONN", lambda: True)
    assert qmgr.qm == "PCMKAPP"
    assert qmgr.cd.ConnectionName == b"vip(1414)"
    assert qmgr.opts == 0x4000000  # MQCNO_RECONNECT_Q_MGR


def test_connect_retry_raises_a_non_failover_error(monkeypatch):
    fake = _fake_pymqi([2035])  # MQRC_NOT_AUTHORIZED: not retryable
    monkeypatch.setitem(sys.modules, "pymqi", fake)
    with pytest.raises(fake.MQMIError):
        dr_mqi.connect_retry("PCMKAPP", "vip(1414)", "APP.SVRCONN", lambda: True)


def test_connect_retry_gives_up_when_told_to(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymqi", _fake_pymqi([]))
    assert dr_mqi.connect_retry("PCMKAPP", "vip(1414)", "APP.SVRCONN", lambda: False) is None
