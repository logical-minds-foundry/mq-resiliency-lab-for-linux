from mqlab.dr import (
    Event,
    Ledger,
    LedgerEntry,
    assert_self_correct,
    build_report,
    reconcile,
)
from mqlab.dr.model import Bucket


def _round_trip(firm, dtcc, seq, t):
    """Helper: a clean confirmed round trip for one message at time base t."""
    uuid = f"u{seq}"
    firm.append(LedgerEntry(Event.SENT, seq, uuid, t))
    dtcc.append(LedgerEntry(Event.RECEIVED, seq, uuid, t + 0.1))
    dtcc.append(LedgerEntry(Event.REPLIED, seq, uuid, t + 0.2))
    firm.append(LedgerEntry(Event.CONFIRMED, seq, uuid, t + 0.3))


def test_no_fault_run_is_self_correct():
    firm, dtcc = Ledger(), Ledger()
    for seq in range(1, 51):
        _round_trip(firm, dtcc, seq, float(seq))
    facts = reconcile(
        firm,
        dtcc,
        secondary_present=set(range(1, 51)),
        primary_disk_present=set(),
        cutover_ts=1_000.0,
    )
    assert_self_correct(facts)  # the instrument agrees with itself


def test_forced_dr_produces_classified_loss():
    firm, dtcc = Ledger(), Ledger()
    # seqs 1..40 complete cleanly before cutover
    for seq in range(1, 41):
        _round_trip(firm, dtcc, seq, float(seq))
    # seq 41: DTCC processed it, reply lost (Ambiguous)
    firm.append(LedgerEntry(Event.SENT, 41, "u41", 41.0))
    dtcc.append(LedgerEntry(Event.RECEIVED, 41, "u41", 41.1))
    dtcc.append(LedgerEntry(Event.REPLIED, 41, "u41", 41.2))
    # seq 42: stranded on the dead primary, never reached DTCC
    firm.append(LedgerEntry(Event.SENT, 42, "u42", 42.0))

    facts = reconcile(
        firm,
        dtcc,
        secondary_present=set(range(1, 41)),  # only the completed ones replicated
        primary_disk_present={42},
        cutover_ts=41.5,
    )
    rep = build_report("DR-FORCE-1", "C", facts, peak_exposure=2)
    assert rep.rpo_zero is False
    assert rep.census[Bucket.CONFIRMED] == 40
    assert rep.census[Bucket.AMBIGUOUS] == 1
    assert rep.census[Bucket.STRANDED] == 1
    assert rep.window == (41, 42, 2)
