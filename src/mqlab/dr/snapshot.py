"""Post-event queue snapshots.

The pure half (`seqs_from_bodies`) turns a list of browsed message bodies into
the set of DR sequence numbers present — used to build `secondary_present` and
`primary_disk_present` for reconcile(). The live browse that produces those
bodies (pymqi) lives in browse_queue() and runs only on the lab.
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
