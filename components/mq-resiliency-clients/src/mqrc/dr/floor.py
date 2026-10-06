"""The lab evidence floor (spec §2 goal 7).

A run counts as "RPO 0 under load" only if it clears this floor, and paired arms
must run the identical (rate, seconds) for the cross-arm comparison to be fair.
Numbers are PROVISIONAL pending a lab-capacity check (spec §9 Q2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ledger import Event

if TYPE_CHECKING:
    from .ledger import Ledger


@dataclass(frozen=True)
class Floor:
    min_rate: float
    min_seconds: float
    min_total: int


FLOOR = Floor(min_rate=20.0, min_seconds=300.0, min_total=6000)


@dataclass(frozen=True)
class FloorResult:
    ok: bool
    rate: float
    seconds: float
    total: int
    reason: str


def meets_floor(app: Ledger, floor: Floor) -> FloorResult:
    sent = sorted(e.ts for e in app.entries if e.event is Event.SENT)
    total = len(sent)
    seconds = (sent[-1] - sent[0]) if total >= 2 else 0.0
    rate = (total / seconds) if seconds > 0 else 0.0
    problems = []
    if total < floor.min_total:
        problems.append(f"total {total} < {floor.min_total}")
    if seconds < floor.min_seconds:
        problems.append(f"seconds {seconds:.0f} < {floor.min_seconds:.0f}")
    if rate < floor.min_rate:
        problems.append(f"rate {rate:.1f} < {floor.min_rate:.1f}")
    return FloorResult(
        ok=not problems,
        rate=rate,
        seconds=seconds,
        total=total,
        reason="; ".join(problems) or "ok",
    )
