"""Exposure gauge — the exact app-layer at-risk count.

exposure(T) = count of messages the app has SENT (local QM ACKed) but not yet
had reply-CONFIRMED, as of time T. This is the ONLY exposure number we claim;
the spec (§4.4) is explicit that the replication gap is NOT message-attributable
from block-level replication, so we never estimate it.
"""

from __future__ import annotations

from .ledger import Event, Ledger


def unresolved_seqs(app: Ledger, at_ts: float | None = None) -> set[int]:
    sent = {
        e.seq for e in app.entries if e.event is Event.SENT and (at_ts is None or e.ts <= at_ts)
    }
    confirmed = app.confirmed_seqs(at_ts=at_ts)
    return sent - confirmed


def exposure(app: Ledger, at_ts: float | None = None) -> int:
    return len(unresolved_seqs(app, at_ts=at_ts))


def peak_exposure(app: Ledger) -> int:
    """Max concurrent in-flight (SENT but not yet CONFIRMED) over the whole run,
    by replaying the timestamped ledger. At equal timestamps a SENT is counted
    before a CONFIRMED (conservative — never under-reports the peak).
    """
    events: list[tuple[float, int]] = []
    for e in app.entries:
        if e.event is Event.SENT:
            events.append((e.ts, +1))
        elif e.event is Event.CONFIRMED:
            events.append((e.ts, -1))
    events.sort(key=lambda x: (x[0], -x[1]))  # +1 before -1 at the same ts
    inflight = peak = 0
    for _, delta in events:
        inflight += delta
        peak = max(peak, inflight)
    return peak
