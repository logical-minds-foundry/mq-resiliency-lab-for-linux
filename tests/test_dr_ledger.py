from mqlab.dr.ledger import Event, Ledger, LedgerEntry
from mqlab.dr.model import MessageState


def test_append_and_roundtrip_jsonl(tmp_path):
    lg = Ledger()
    lg.append(LedgerEntry(event=Event.SENT, seq=1, uuid="u1", ts=1.0))
    lg.append(LedgerEntry(event=Event.CONFIRMED, seq=1, uuid="u1", ts=2.0))
    path = tmp_path / "app.jsonl"
    lg.write_jsonl(path)

    # one JSON object per line, append-only
    lines = path.read_text().splitlines()
    assert len(lines) == 2

    back = Ledger.read_jsonl(path)
    assert back.entries == lg.entries


def test_read_jsonl_missing_file_is_empty(tmp_path):
    back = Ledger.read_jsonl(tmp_path / "nope.jsonl")
    assert back.entries == []


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "app.jsonl"
    lg = Ledger([LedgerEntry(Event.SENT, 1, "u1", 1.0)])
    lg.write_jsonl(path)
    # inject a trailing blank line that read_jsonl must skip
    path.write_text(path.read_text() + "\n")
    back = Ledger.read_jsonl(path)
    assert back.entries == lg.entries


def test_firm_states_folds_events():
    lg = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
            LedgerEntry(Event.SENT, 2, "u2", 3.0),  # no reply -> in pipeline
        ]
    )
    states = lg.app_states()
    assert states[1] == MessageState.CONFIRMED
    assert states[2] == MessageState.IN_PIPELINE


def test_dtcc_receive_counts_count_duplicates():
    lg = Ledger(
        [
            LedgerEntry(Event.RECEIVED, 1, "u1", 1.0),
            LedgerEntry(Event.REPLIED, 1, "u1", 1.5),
            LedgerEntry(Event.RECEIVED, 1, "u1", 9.0),  # a second receive of the same msg
            LedgerEntry(Event.RECEIVED, 2, "u2", 2.0),
        ]
    )
    assert lg.svc_receive_counts() == {1: 2, 2: 1}
    assert lg.svc_replied() == {1}


def test_sent_seqs_and_confirmed_seqs():
    lg = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 2.0),
            LedgerEntry(Event.SENT, 2, "u2", 3.0),
        ]
    )
    assert lg.sent_seqs() == {1, 2}
    assert lg.confirmed_seqs() == {1}


def test_uuid_of_maps_seq_to_uuid():
    lg = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.SENT, 2, "u2", 3.0),
        ]
    )
    assert lg.uuid_of() == {1: "u1", 2: "u2"}


def test_confirmed_seqs_honours_at_ts():
    lg = Ledger(
        [
            LedgerEntry(Event.SENT, 1, "u1", 1.0),
            LedgerEntry(Event.CONFIRMED, 1, "u1", 9.0),  # after the cutoff
        ]
    )
    assert lg.confirmed_seqs(at_ts=5.0) == set()
