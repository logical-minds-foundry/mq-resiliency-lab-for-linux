from mqlab.dr.floor import FLOOR, FloorResult, meets_floor
from mqlab.dr.ledger import Event, Ledger, LedgerEntry


def _firm_with(n, rate):
    # n SENT messages, one every 1/rate s, starting at t=0
    lg = Ledger()
    for i in range(1, n + 1):
        lg.append(LedgerEntry(Event.SENT, i, f"u{i}", (i - 1) / rate))
    return lg


def test_meets_floor_true_when_rate_duration_volume_satisfied():
    firm = _firm_with(n=7000, rate=20.0)  # 7000 msgs over ~350 s at 20/s
    res = meets_floor(firm, FLOOR)
    assert isinstance(res, FloorResult)
    assert res.ok is True
    assert res.total == 7000


def test_meets_floor_false_when_too_few_messages():
    firm = _firm_with(n=100, rate=20.0)
    res = meets_floor(firm, FLOOR)
    assert res.ok is False
    assert "total" in res.reason


def test_floor_defaults_are_the_provisional_values():
    assert FLOOR.min_rate == 20.0
    assert FLOOR.min_seconds == 300.0
    assert FLOOR.min_total == 6000


def test_meets_floor_empty_ledger_has_zero_rate_and_duration():
    # fewer than 2 SENT entries -> seconds and rate are 0.0, all checks fail
    res = meets_floor(Ledger(), FLOOR)
    assert res.ok is False
    assert res.seconds == 0.0
    assert res.rate == 0.0
    assert "rate" in res.reason and "seconds" in res.reason
