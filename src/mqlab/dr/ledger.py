"""Append-only ledger of message events, persisted as JSONL.

The FIRM ledger records SENT (MQPUT+commit OK) and CONFIRMED (reply matched).
The DTCC god's-eye ledger records RECEIVED (per receive, counting duplicates)
and REPLIED.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path


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
