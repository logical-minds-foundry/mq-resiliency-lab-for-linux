"""Post-event queue snapshots.

`seqs_from_bodies` turns a list of browsed message bodies into the set of DR
sequence numbers present — used to build `secondary_present` and
`primary_disk_present` for reconcile(). This module only parses bodies the caller
has already browsed; it makes no MQI calls.
"""

from __future__ import annotations

from .wire import parse_body


def seqs_from_bodies(bodies: list[bytes]) -> set[int]:
    seqs: set[int] = set()
    for b in bodies:
        try:
            seqs.add(parse_body(b).seq)
        except ValueError:
            continue  # foreign / non-DR message — not ours to count
    return seqs
