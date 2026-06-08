"""Reporting: census, loss window, self-correctness, ScenarioReport, cross-arm.

The loss window is measured in SEQUENCE space (exact, clock-free), per spec §7;
wall-clock spans are context only and are not computed here.
"""

from __future__ import annotations

from collections import Counter

from .classifier import classify
from .model import Bucket, MessageFacts

# Buckets that represent a healthy outcome; everything else is "at risk".
SAFE_BUCKETS = frozenset({Bucket.CONFIRMED, Bucket.CONTINUED})


def census(facts: list[MessageFacts]) -> dict[Bucket, int]:
    counts: Counter[Bucket] = Counter()
    for f in facts:
        counts[classify(f)] += 1
    return {b: counts.get(b, 0) for b in Bucket}


def loss_window(facts: list[MessageFacts]) -> tuple[int, int, int] | None:
    at_risk = [f.seq for f in facts if classify(f) not in SAFE_BUCKETS]
    if not at_risk:
        return None
    return (min(at_risk), max(at_risk), len(at_risk))


class SelfCorrectnessError(Exception):
    """Raised when a no-fault baseline run is not all-Confirmed — the instrument
    (or the lab) is broken and no drill result can be trusted until it is fixed.
    """


def self_correctness_violations(facts: list[MessageFacts]) -> list[MessageFacts]:
    return [f for f in facts if classify(f) is not Bucket.CONFIRMED]


def assert_self_correct(facts: list[MessageFacts]) -> None:
    bad = self_correctness_violations(facts)
    if bad:
        seqs = ", ".join(str(f.seq) for f in bad)
        raise SelfCorrectnessError(
            f"no-fault baseline has {len(bad)} non-confirmed message(s): seq {seqs}"
        )
