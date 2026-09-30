"""Perf record for a lab run: where the wall-clock (and, later, host contention) goes.

`PerfRecord` collects the per-step timings the orchestrator already measures, grouped by
phase (net / vms / provision / observe), plus named milestones (e.g. OpenSearch
time-to-green) and free-text `notes` recording any degraded/unavailable sample. It
serialises to stable JSON (the artifact that runs are diffed on) and to a human summary.

`PerfSink` is the narrow interface `orchestrator.run_steps` feeds; a `PerfRecord` is
one, and `NullSink` is the no-op default for callers that don't collect. The report is
additive: collecting it never changes how a run behaves (epic #275 spec §1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mqlab.perfsampler import GuestSample

_UNPHASED = "(unphased)"


class PerfSink(Protocol):
    """Receives one completed step's timing from the run loop."""

    def step(self, phase: str, label: str, seconds: float, retries: int) -> None: ...


class NullSink:
    """A PerfSink that discards everything — the default when nothing is collected."""

    def step(self, phase: str, label: str, seconds: float, retries: int) -> None:
        del phase, label, seconds, retries  # intentionally discarded


@dataclass(frozen=True)
class StepTiming:
    """One completed step: its phase, label, successful-attempt seconds, retry count."""

    phase: str
    label: str
    seconds: float
    retries: int


@dataclass
class _PhaseTotal:
    """Accumulator for one phase: summed seconds/retries plus the per-step detail."""

    seconds: float = 0.0
    retries: int = 0
    steps: list[StepTiming] = field(default_factory=list)

    def add(self, s: StepTiming) -> None:
        self.seconds += s.seconds
        self.retries += s.retries
        self.steps.append(s)

    def as_dict(self) -> dict[str, object]:
        return {
            "seconds": self.seconds,
            "retries": self.retries,
            "steps": [
                {"label": s.label, "seconds": s.seconds, "retries": s.retries} for s in self.steps
            ],
        }


@dataclass(frozen=True)
class HostContentionSample:
    """One sampler tick (#1203): seconds since the run started, host CPU/iowait % (None
    when the host probe failed), and each guest's vCPU steal % (None = baseline reading;
    a guest whose probe failed is absent)."""

    t: float
    host_cpu: float | None
    host_iowait: float | None
    host_cpus: int | None
    guest_steal: dict[str, float | None]

    def as_dict(self) -> dict[str, object]:
        return {
            "t": self.t,
            "host": {"cpu": self.host_cpu, "iowait": self.host_iowait, "cpus": self.host_cpus},
            "guests": {name: {"steal": s} for name, s in self.guest_steal.items()},
        }


def _mean_peak(label: str, values: list[float | None]) -> str:
    """One summary line: mean/peak over the real readings, or say there were none."""
    real = [v for v in values if v is not None]
    if not real:
        return f"  {label:<24} no reading"
    mean = sum(real) / len(real)
    return f"  {label:<24} mean {mean:.1f}%  peak {max(real):.1f}%  ({len(real)} reading(s))"


@dataclass
class PerfRecord:
    """The perf data for one run. Mutable by design: the run appends as it goes."""

    stack: str
    started_at: float
    steps: list[StepTiming] = field(default_factory=list)
    milestones: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    samples: list[HostContentionSample] = field(default_factory=list)

    def note(self, text: str) -> None:
        """Record a degraded/unavailable measurement — the report says so, never hides it."""
        self.notes.append(text)

    def add_sample(
        self,
        t: float,
        host_cpu: float | None,
        host_iowait: float | None,
        guests: Mapping[str, GuestSample],
        host_cpus: int | None = None,
    ) -> None:
        """Append one host-contention sample (the perf sampler's tick, #1203)."""
        steal = {name: g.steal_pct for name, g in guests.items()}
        self.samples.append(HostContentionSample(t, host_cpu, host_iowait, host_cpus, steal))

    def add_step(self, phase: str, label: str, seconds: float, retries: int) -> None:
        self.steps.append(StepTiming(phase, label, seconds, retries))

    def step(self, phase: str, label: str, seconds: float, retries: int) -> None:
        """PerfSink conformance, so a record can be passed straight to run_steps."""
        self.add_step(phase, label, seconds, retries)

    def add_milestone(self, name: str, seconds: float) -> None:
        self.milestones[name] = seconds

    def _phases(self) -> dict[str, _PhaseTotal]:
        """Steps grouped by phase, in first-seen order, with summed seconds/retries."""
        phases: dict[str, _PhaseTotal] = {}
        for s in self.steps:
            phases.setdefault(s.phase, _PhaseTotal()).add(s)
        return phases

    def _samples_summary(self) -> list[str]:
        """Host CPU/iowait and per-guest steal as mean/peak lines (empty if none sampled)."""
        if not self.samples:
            return []
        lines = [
            f"Samples: {len(self.samples)}",
            _mean_peak("host cpu", [s.host_cpu for s in self.samples]),
            _mean_peak("host iowait", [s.host_iowait for s in self.samples]),
        ]
        steal: dict[str, list[float | None]] = {}
        for s in self.samples:
            for name, value in s.guest_steal.items():
                steal.setdefault(name, []).append(value)
        lines.extend(_mean_peak(f"steal {name}", values) for name, values in steal.items())
        return lines

    def to_json(self) -> str:
        """Stable JSON: fixed top-level key order, phases/steps in run order."""
        data = {
            "stack": self.stack,
            "started_at": self.started_at,
            "phases": {name: total.as_dict() for name, total in self._phases().items()},
            "milestones": dict(self.milestones),
            "notes": list(self.notes),
            "samples": [s.as_dict() for s in self.samples],
        }
        return json.dumps(data, indent=2)

    def human_summary(self) -> str:
        """A short plain-text table: per-phase totals, then milestones, then notes."""
        lines = [f"Perf summary — {self.stack}"]
        phases = self._phases()
        if not phases:
            lines.append("  (no steps recorded)")
        for name, total in phases.items():
            lines.append(
                f"  {name or _UNPHASED:<14} {total.seconds:>10.2f}s  "
                f"{len(total.steps)} step(s), {total.retries} retr(y/ies)"
            )
        if self.milestones:
            lines.append("Milestones:")
            lines.extend(f"  {n:<24} {s:>10.2f}s" for n, s in self.milestones.items())
        lines.extend(self._samples_summary())
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"  - {note}" for note in self.notes)
        return "\n".join(lines)
