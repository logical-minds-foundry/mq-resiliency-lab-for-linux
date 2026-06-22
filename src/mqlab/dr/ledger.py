"""Append-only ledger of message events, persisted as JSONL.

The FIRM ledger records SENT (MQPUT+commit OK) and CONFIRMED (reply matched).
The DTCC Watcher ledger records RECEIVED (per receive, counting duplicates)
and REPLIED.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .model import MessageState


class Event(StrEnum):
    SENT = "sent"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    REPLIED = "replied"


@dataclass(frozen=True)
class LedgerEntry:
    event: Event
    seq: int
    uuid: str
    ts: float


class Ledger:
    def __init__(self, entries: list[LedgerEntry] | None = None) -> None:
        self.entries: list[LedgerEntry] = list(entries or [])

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def write_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as fh:
            for e in self.entries:
                row = asdict(e)
                row["event"] = e.event.value
                fh.write(json.dumps(row) + "\n")

    @classmethod
    def read_jsonl(cls, path: str | Path) -> Ledger:
        p = Path(path)
        if not p.exists():
            return cls()
        entries: list[LedgerEntry] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            entries.append(
                LedgerEntry(
                    event=Event(row["event"]),
                    seq=int(row["seq"]),
                    uuid=str(row["uuid"]),
                    ts=float(row["ts"]),
                )
            )
        return cls(entries)

    def sent_seqs(self) -> set[int]:
        return {e.seq for e in self.entries if e.event is Event.SENT}

    def confirmed_seqs(self, at_ts: float | None = None) -> set[int]:
        return {
            e.seq
            for e in self.entries
            if e.event is Event.CONFIRMED and (at_ts is None or e.ts <= at_ts)
        }

    def uuid_of(self) -> dict[int, str]:
        return {e.seq: e.uuid for e in self.entries}

    def app_states(self) -> dict[int, MessageState]:
        sent = self.sent_seqs()
        confirmed = self.confirmed_seqs()
        states: dict[int, MessageState] = {}
        for seq in sent:
            states[seq] = MessageState.CONFIRMED if seq in confirmed else MessageState.IN_PIPELINE
        return states

    def svc_receive_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for e in self.entries:
            if e.event is Event.RECEIVED:
                counts[e.seq] = counts.get(e.seq, 0) + 1
        return counts

    def svc_replied(self) -> set[int]:
        return {e.seq for e in self.entries if e.event is Event.REPLIED}
