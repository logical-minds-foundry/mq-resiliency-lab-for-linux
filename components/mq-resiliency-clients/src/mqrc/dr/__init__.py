"""DR/HA validation framework — pure-Python core (ledger, classifier, reporting)."""

from .classifier import classify, classify_all
from .exposure import exposure, peak_exposure, unresolved_seqs
from .floor import FLOOR, FloorResult, meets_floor
from .ledger import Event, Ledger, LedgerEntry
from .model import Bucket, MessageFacts, MessageState
from .reconcile import reconcile
from .report import (
    ScenarioReport,
    SelfCorrectnessError,
    assert_self_correct,
    build_report,
    census,
    cross_arm,
    loss_window,
    self_correctness_violations,
)

__all__ = [
    "FLOOR",
    "Bucket",
    "Event",
    "FloorResult",
    "Ledger",
    "LedgerEntry",
    "MessageFacts",
    "MessageState",
    "ScenarioReport",
    "SelfCorrectnessError",
    "assert_self_correct",
    "build_report",
    "census",
    "classify",
    "classify_all",
    "cross_arm",
    "exposure",
    "loss_window",
    "meets_floor",
    "peak_exposure",
    "reconcile",
    "self_correctness_violations",
    "unresolved_seqs",
]
