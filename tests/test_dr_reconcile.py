from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.reconcile import reconcile


def test_reconcile_builds_one_fact_per_sent_message():
    firm = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),  # confirmed pre-cutover
            LedgerEntry(Event.SENT, 2, "u2", 3.0),  # in pipeline at cutover
            LedgerEntry(Event.SENT, 3, "u3", 4.0),  # stranded
        ]
    )
    dtcc = Ledger(
        [
            LedgerEntry(Event.RECEIVED, 1, "u1", 1.5),
            LedgerEntry(Event.REPLIED, 1, "u1", 1.8),
            LedgerEntry(Event.RECEIVED, 2, "u2", 3.5),  # DTCC got it, reply lost
            LedgerEntry(Event.REPLIED, 2, "u2", 3.8),
        ]
    )
    facts = reconcile(
        firm,
        dtcc,
        secondary_present=set(),  # nothing replicated
        primary_disk_present={3},  # seq 3 still on the dead box
        cutover_ts=2.5,
    )
    by_seq = {f.seq: f for f in facts}
    assert set(by_seq) == {1, 2, 3}

    assert by_seq[1].firm_confirmed is True and by_seq[1].dtcc_received == 1
    assert by_seq[2].firm_confirmed is False and by_seq[2].dtcc_received == 1
    assert by_seq[3].dtcc_received == 0 and by_seq[3].on_primary_disk is True


def test_confirm_after_cutover_is_not_pre_cutover_confirmed():
    firm = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 9.0),  # reply arrived AFTER cutover
        ]
    )
    facts = reconcile(
        firm,
        Ledger(),
        secondary_present={1},
        primary_disk_present=set(),
        cutover_ts=5.0,
    )
    assert facts[0].firm_confirmed is False  # not confirmed at cutover
    assert facts[0].on_secondary is True  # but it did replicate


def test_reconcile_defaults_uuid_to_empty_when_absent():
    # a SENT with no recorded uuid mapping still yields a fact (uuid -> "")
    firm = Ledger([LedgerEntry(Event.SENT, 1, "", 1.0)])
    facts = reconcile(
        firm,
        Ledger(),
        secondary_present=set(),
        primary_disk_present=set(),
        cutover_ts=5.0,
    )
    assert facts[0].uuid == ""
