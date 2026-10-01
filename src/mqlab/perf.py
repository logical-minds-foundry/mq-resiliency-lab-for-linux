"""Perf record for a lab run: where the wall-clock (and, later, host contention) goes.

`PerfRecord` collects the per-step timings the orchestrator already measures, grouped by
phase (preflight / net / vms / provision / observe; a failed step is kept, marked
`ok: false`, #1215), plus named milestones (e.g. OpenSearch
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

    from mqlab.perfsampler import GuestSample, LocalSample

_UNPHASED = "(unphased)"


class PerfSink(Protocol):
    """Receives one finished step's timing from the run loop (`ok=False` = it failed)."""

    def step(
        self, phase: str, label: str, seconds: float, retries: int, *, ok: bool = True
    ) -> None: ...


class NullSink:
    """A PerfSink that discards everything — the default when nothing is collected."""

    def step(
        self, phase: str, label: str, seconds: float, retries: int, *, ok: bool = True
    ) -> None:
        del phase, label, seconds, retries, ok  # intentionally discarded


@dataclass(frozen=True)
class StepTiming:
    """One finished step: its phase, label, final-attempt seconds, retry count, and
    whether it succeeded. A failed step (#1215) carries its LAST attempt's seconds —
    the same final-attempt semantics as a success — and still counts in its phase."""

    phase: str
    label: str
    seconds: float
    retries: int
    ok: bool = True


@dataclass
class _PhaseTotal:
    """Accumulator for one phase: summed seconds/retries plus the per-step detail."""

    seconds: float = 0.0
    retries: int = 0
    failed: int = 0
    steps: list[StepTiming] = field(default_factory=list)

    def add(self, s: StepTiming) -> None:
        self.seconds += s.seconds
        self.retries += s.retries
        self.failed += 0 if s.ok else 1
        self.steps.append(s)

    def as_dict(self) -> dict[str, object]:
        return {
            "seconds": self.seconds,
            "retries": self.retries,
            "steps": [
                {"label": s.label, "seconds": s.seconds, "retries": s.retries, "ok": s.ok}
                for s in self.steps
            ],
            "failed": self.failed,
        }


@dataclass(frozen=True)
class HostContentionSample:
    """One sampler tick (#1203): seconds since the run started, host CPU/iowait % (None
    when the host probe failed), the sampling (Vergil) VM's own steal/busy/iowait % from
    its local /proc/stat (#1215; None = no reading this tick), and each guest's reading
    (a guest whose probe failed is absent)."""

    t: float
    host_cpu: float | None
    host_iowait: float | None
    host_cpus: int | None
    guests: dict[str, GuestSample]
    local: LocalSample | None = None

    def as_dict(self) -> dict[str, object]:
        loc = self.local
        return {
            "t": self.t,
            "host": {
                "cpu": self.host_cpu,
                "iowait": self.host_iowait,
                "cpus": self.host_cpus,
                "self_steal": loc.steal_pct if loc else None,
                "self_busy": loc.busy_pct if loc else None,
                "self_iowait": loc.iowait_pct if loc else None,
            },
            "guests": {
                name: {
                    "steal": g.steal_pct,
                    "busy": g.busy_pct,
                    "iowait": g.iowait_pct,
                    "load1": g.load1,
                    "load5": g.load5,
                    "load15": g.load15,
                }
                for name, g in self.guests.items()
            },
        }


def _mean_peak(label: str, values: list[float | None], unit: str = "%") -> str:
    """One summary line: mean/peak over the real readings, or say there were none.
    Percentages print to 0.1; a unitless reading (load average) to 0.01."""
    real = [v for v in values if v is not None]
    if not real:
        return f"  {label:<24} no reading"
    mean = sum(real) / len(real)
    digits = 1 if unit else 2
    return (
        f"  {label:<24} mean {mean:.{digits}f}{unit}  peak {max(real):.{digits}f}{unit}  "
        f"({len(real)} reading(s))"
    )


# Per-guest summary metrics: (label prefix, GuestSample attribute, unit).
_GUEST_METRICS = (
    ("steal", "steal_pct", "%"),
    ("busy", "busy_pct", "%"),
    ("iowait", "iowait_pct", "%"),
    ("load1", "load1", ""),
)


@dataclass
class PerfRecord:
    """The perf data for one run. Mutable by design: the run appends as it goes."""

    stack: str
    started_at: float
    steps: list[StepTiming] = field(default_factory=list)
    milestones: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    samples: list[HostContentionSample] = field(default_factory=list)
    # The effective MQLAB_ENV profile and how it was chosen (#1245): env None = the base
    # topology; env_source is explicit / detected / inconclusive (None = not recorded).
    env: str | None = None
    env_source: str | None = None

    def set_env(self, env: str | None, source: str) -> None:
        """Record which env profile this run used and its source (#1245)."""
        self.env = env
        self.env_source = source

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
        local: LocalSample | None = None,
    ) -> None:
        """Append one host-contention sample (the perf sampler's tick, #1203/#1215)."""
        self.samples.append(
            HostContentionSample(t, host_cpu, host_iowait, host_cpus, dict(guests), local)
        )

    def add_step(
        self, phase: str, label: str, seconds: float, retries: int, *, ok: bool = True
    ) -> None:
        self.steps.append(StepTiming(phase, label, seconds, retries, ok))

    def step(
        self, phase: str, label: str, seconds: float, retries: int, *, ok: bool = True
    ) -> None:
        """PerfSink conformance, so a record can be passed straight to run_steps."""
        self.add_step(phase, label, seconds, retries, ok=ok)

    def add_milestone(self, name: str, seconds: float) -> None:
        self.milestones[name] = seconds

    def _phases(self) -> dict[str, _PhaseTotal]:
        """Steps grouped by phase, in first-seen order, with summed seconds/retries."""
        phases: dict[str, _PhaseTotal] = {}
        for s in self.steps:
            phases.setdefault(s.phase, _PhaseTotal()).add(s)
        return phases

    def _samples_summary(self) -> list[str]:
        """Host CPU/iowait, the Vergil VM's own steal/busy/iowait, and per-guest
        steal/busy/iowait/load1 as mean/peak lines (empty if none sampled)."""
        if not self.samples:
            return []
        locs = [s.local for s in self.samples]
        lines = [
            f"Samples: {len(self.samples)}",
            _mean_peak("host cpu", [s.host_cpu for s in self.samples]),
            _mean_peak("host iowait", [s.host_iowait for s in self.samples]),
            _mean_peak("vergil-vm steal", [x.steal_pct if x else None for x in locs]),
            _mean_peak("vergil-vm busy", [x.busy_pct if x else None for x in locs]),
            _mean_peak("vergil-vm iowait", [x.iowait_pct if x else None for x in locs]),
        ]
        per_guest: dict[str, list[GuestSample]] = {}
        for s in self.samples:
            for name, g in s.guests.items():
                per_guest.setdefault(name, []).append(g)
        for name, readings in per_guest.items():
            lines.extend(
                _mean_peak(f"{label} {name}", [getattr(g, attr) for g in readings], unit)
                for label, attr, unit in _GUEST_METRICS
            )
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
            "env": self.env,
            "env_source": self.env_source,
        }
        return json.dumps(data, indent=2)

    def human_summary(self) -> str:
        """A short plain-text table: per-phase totals, then milestones, then notes."""
        lines = [f"Perf summary — {self.stack}"]
        if self.env_source is not None:
            lines.append(f"  env: {self.env or 'base'} ({self.env_source})")
        phases = self._phases()
        if not phases:
            lines.append("  (no steps recorded)")
        for name, total in phases.items():
            failed = f", {total.failed} FAILED" if total.failed else ""
            lines.append(
                f"  {name or _UNPHASED:<14} {total.seconds:>10.2f}s  "
                f"{len(total.steps)} step(s), {total.retries} retr(y/ies){failed}"
            )
        if self.milestones:
            lines.append("Milestones:")
            lines.extend(f"  {n:<24} {s:>10.2f}s" for n, s in self.milestones.items())
        lines.extend(self._samples_summary())
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"  - {note}" for note in self.notes)
        return "\n".join(lines)
