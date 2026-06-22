from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.reconcile import reconcile


def test_reconcile_builds_one_fact_per_sent_message():
    app = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),  # confirmed pre-cutover
            LedgerEntry(Event.SENT, 2, "u2", 3.0),  # in pipeline at cutover
            LedgerEntry(Event.SENT, 3, "u3", 4.0),  # stranded
        ]
    )
    svc = Ledger(
        [
            LedgerEntry(Event.RECEIVED, 1, "u1", 1.5),
            LedgerEntry(Event.REPLIED, 1, "u1", 1.8),
            LedgerEntry(Event.RECEIVED, 2, "u2", 3.5),  # SVC got it, reply lost
            LedgerEntry(Event.REPLIED, 2, "u2", 3.8),
        ]
    )
    facts = reconcile(
        app,
        svc,
        secondary_present=set(),  # nothing replicated
        primary_disk_present={3},  # seq 3 still on the dead box
        cutover_ts=2.5,
    )
    by_seq = {f.seq: f for f in facts}
    assert set(by_seq) == {1, 2, 3}

    assert by_seq[1].app_confirmed is True and by_seq[1].svc_received == 1
    assert by_seq[2].app_confirmed is False and by_seq[2].svc_received == 1
    assert by_seq[3].svc_received == 0 and by_seq[3].on_primary_disk is True


def test_confirm_after_cutover_is_not_pre_cutover_confirmed():
    app = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 9.0),  # reply arrived AFTER cutover
        ]
    )
    facts = reconcile(
        app,
        Ledger(),
        secondary_present={1},
        primary_disk_present=set(),
        cutover_ts=5.0,
    )
    assert facts[0].app_confirmed is False  # not confirmed at cutover
    assert facts[0].on_secondary is True  # but it did replicate


def test_reconcile_defaults_uuid_to_empty_when_absent():
    # a SENT with no recorded uuid mapping still yields a fact (uuid -> "")
    app = Ledger([LedgerEntry(Event.SENT, 1, "", 1.0)])
    facts = reconcile(
        app,
        Ledger(),
        secondary_present=set(),
        primary_disk_present=set(),
        cutover_ts=5.0,
    )
    assert facts[0].uuid == ""
