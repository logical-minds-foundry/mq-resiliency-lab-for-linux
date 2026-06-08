"""Reporting: census, loss window, self-correctness, ScenarioReport, cross-arm.

The loss window is measured in SEQUENCE space (exact, clock-free), per spec §7;
wall-clock spans are context only and are not computed here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class ScenarioReport:
    scenario_id: str
    arm: str
    census: dict[Bucket, int]
    window: tuple[int, int, int] | None
    peak_exposure: int
    # §7 fields — populated by the live runner (Plan 2); default-None/False so
    # pure-core callers and tests need not supply them.
    exposure_at_fault: int | None = None
    rto_seconds: float | None = None
    intervention_required: bool = False
    integrity_anomaly: bool = False
    diagnostics_captured: bool = False

    @property
    def rpo_zero(self) -> bool:
        return self.window is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "arm": self.arm,
            "census": {b.value: n for b, n in self.census.items()},
            "window": list(self.window) if self.window else None,
            "peak_exposure": self.peak_exposure,
            "exposure_at_fault": self.exposure_at_fault,
            "rto_seconds": self.rto_seconds,
            "intervention_required": self.intervention_required,
            "integrity_anomaly": self.integrity_anomaly,
            "diagnostics_captured": self.diagnostics_captured,
            "rpo_zero": self.rpo_zero,
        }

    def to_markdown(self) -> str:
        lines = [
            f"### {self.scenario_id} — arm {self.arm}",
            "",
            f"- RPO 0: **{self.rpo_zero}**",
            f"- RTO: {self.rto_seconds} s",
            f"- Peak exposure: {self.peak_exposure}  (at fault: {self.exposure_at_fault})",
            f"- Intervention required: {self.intervention_required}",
            f"- Integrity anomaly: {self.integrity_anomaly}",
            f"- Diagnostics captured: {self.diagnostics_captured}",
        ]
        if self.window:
            lo, hi, n = self.window
            lines.append(f"- Loss window: messages {lo}–{hi} = {n} messages")
        lines.append("")
        lines.append("| Bucket | Count |")
        lines.append("|---|---|")
        for b in Bucket:
            lines.append(f"| {b.value} | {self.census[b]} |")
        return "\n".join(lines)


def build_report(
    scenario_id: str,
    arm: str,
    facts: list[MessageFacts],
    peak_exposure: int,
    *,
    exposure_at_fault: int | None = None,
    rto_seconds: float | None = None,
    intervention_required: bool = False,
    integrity_anomaly: bool = False,
    diagnostics_captured: bool = False,
) -> ScenarioReport:
    return ScenarioReport(
        scenario_id=scenario_id,
        arm=arm,
        census=census(facts),
        window=loss_window(facts),
        peak_exposure=peak_exposure,
        exposure_at_fault=exposure_at_fault,
        rto_seconds=rto_seconds,
        intervention_required=intervention_required,
        integrity_anomaly=integrity_anomaly,
        diagnostics_captured=diagnostics_captured,
    )


def cross_arm(c: ScenarioReport, d: ScenarioReport) -> dict[str, Any]:
    if c.scenario_id != d.scenario_id:
        raise ValueError(f"cross_arm needs the same scenario: {c.scenario_id} != {d.scenario_id}")
    return {"scenario_id": c.scenario_id, "C": c.to_dict(), "D": d.to_dict()}
