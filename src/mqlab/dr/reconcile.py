"""Turn raw observations into per-message MessageFacts.

Inputs the live harness (Plan 2) supplies; in Plan 1 they come from fixtures:
  - firm ledger (SENT / CONFIRMED)
  - dtcc Watcher ledger (RECEIVED counts / REPLIED)
  - secondary_present: seqs present/processable on the secondary post-cutover
  - primary_disk_present: seqs physically on the failed primary (post-mortem)
  - cutover_ts: the instant of the fault; a reply CONFIRMED after this did not
    arrive "before cutover"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import MessageFacts

if TYPE_CHECKING:
    from .ledger import Ledger


def reconcile(
    firm: Ledger,
    dtcc: Ledger,
    *,
    secondary_present: set[int],
    primary_disk_present: set[int],
    cutover_ts: float,
) -> list[MessageFacts]:
    uuid_of = firm.uuid_of()
    confirmed_pre = firm.confirmed_seqs(at_ts=cutover_ts)
    svc_counts = dtcc.svc_receive_counts()
    svc_repl = dtcc.svc_replied()

    facts: list[MessageFacts] = []
    for seq in sorted(firm.sent_seqs()):
        facts.append(
            MessageFacts(
                seq=seq,
                uuid=uuid_of.get(seq, ""),
                app_confirmed=seq in confirmed_pre,
                svc_received=svc_counts.get(seq, 0),
                svc_replied=seq in svc_repl,
                on_secondary=seq in secondary_present,
                on_primary_disk=seq in primary_disk_present,
            )
        )
    return facts
