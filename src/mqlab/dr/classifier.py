"""The six-bucket classifier (spec §5).

First match wins — the precedence encodes the reconciliation severity:
a duplicate is always a duplicate; a confirmed round-trip is done; otherwise
prefer the most-recoverable explanation. The ordering is part of the contract
and is locked by tests in tests/test_dr_classifier.py.
"""

from __future__ import annotations

from .model import Bucket, MessageFacts


def classify(f: MessageFacts) -> Bucket:
    if f.svc_received >= 2:
        return Bucket.DUPLICATED
    if f.app_confirmed:
        return Bucket.CONFIRMED
    if f.on_secondary:
        return Bucket.CONTINUED
    if f.svc_received == 1:
        return Bucket.AMBIGUOUS
    if f.on_primary_disk:
        return Bucket.STRANDED
    return Bucket.LOST_UNPROCESSED


def classify_all(facts: list[MessageFacts]) -> list[tuple[MessageFacts, Bucket]]:
    return [(f, classify(f)) for f in facts]
