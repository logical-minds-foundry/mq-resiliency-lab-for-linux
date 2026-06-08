from mqlab.dr.ledger import Event, Ledger, LedgerEntry


def test_append_and_roundtrip_jsonl(tmp_path):
    lg = Ledger()
    lg.append(LedgerEntry(event=Event.SENT, seq=1, uuid="u1", ts=1.0))
    lg.append(LedgerEntry(event=Event.CONFIRMED, seq=1, uuid="u1", ts=2.0))
    path = tmp_path / "firm.jsonl"
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
    path = tmp_path / "firm.jsonl"
    lg = Ledger([LedgerEntry(Event.SENT, 1, "u1", 1.0)])
    lg.write_jsonl(path)
    # inject a trailing blank line that read_jsonl must skip
    path.write_text(path.read_text() + "\n")
    back = Ledger.read_jsonl(path)
    assert back.entries == lg.entries
