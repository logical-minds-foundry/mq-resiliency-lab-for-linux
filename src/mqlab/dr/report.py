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
