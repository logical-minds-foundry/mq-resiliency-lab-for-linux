from mqlab.dr.exposure import exposure, peak_exposure, unresolved_seqs
from mqlab.dr.ledger import Event, Ledger, LedgerEntry


def _firm():
    return Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
            LedgerEntry(Event.SENT, 2, "u2", 3.0),  # in pipeline at ts>=3
            LedgerEntry(Event.SENT, 3, "u3", 4.0),  # in pipeline at ts>=4
            LedgerEntry(Event.CONFIRMED, 3, "u3", 5.0),
        ]
    )


def test_exposure_now_counts_unresolved():
    # final state: only seq 2 never confirmed
    assert unresolved_seqs(_firm()) == {2}
    assert exposure(_firm()) == 1


def test_exposure_at_instant_uses_only_events_up_to_ts():
    # at ts=4: sent {1,2,3}, confirmed {1} -> unresolved {2,3}
    assert unresolved_seqs(_firm(), at_ts=4.0) == {2, 3}
    assert exposure(_firm(), at_ts=4.0) == 2


def test_peak_exposure_is_max_concurrent_in_flight():
    # replay of _firm(): SENT1->1, CONF1->0, SENT2->1, SENT3->2 (peak), CONF3->1
    assert peak_exposure(_firm()) == 2


def test_peak_exposure_ignores_non_firm_events():
    # RECEIVED/REPLIED entries are neither SENT nor CONFIRMED -> contribute nothing
    lg = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.RECEIVED, 1, "u1", 1.5),
            LedgerEntry(Event.REPLIED, 1, "u1", 1.8),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
        ]
    )
    assert peak_exposure(lg) == 1
