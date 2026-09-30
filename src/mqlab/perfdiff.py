"""Diff two perf reports: where the wall-clock (and host contention) diverges (#1204).

Epic .github#275 Task 5. Consumes two `PerfRecord.to_json()` documents (see
`mqlab.perf`) — typically the same commit bootstrapped on macOS/arm64 (A) and x86
cloud (B) — and reports, A against B:

- per-phase seconds: delta (A - B) and ratio (A / B), plus each side's retries (and
  failed-step count, #1215 — a failed step's time is IN its phase's seconds);
- per-milestone seconds: delta and ratio, tolerating a milestone on one side only;
- the dominant divergence: the phase (present on both sides) with the largest |delta|;
- each side's top vCPU-steal contributors, when the host-contention ``samples`` key
  is present (absent -> noted, never fatal);
- each side's mean host CPU/iowait and the sampling (Vergil) VM's own steal/busy/iowait
  (#1215), from the same samples.

It is a comparison aid, deliberately with **no pass/fail verdict**: the two sides run on
different hardware, so the numbers are directional — read the *shape* of the bottleneck,
not exact seconds (spec §3 "grain of salt").

A malformed report (missing ``phases``/``milestones``, a non-numeric duration) raises
`PerfDiffError` rather than being skipped: a silently-dropped phase would make the diff lie.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

TOP_STEAL = 5
"""How many steal contributors to list per side."""

_UNPHASED = "(unphased)"


class PerfDiffError(ValueError):
    """A perf report could not be read or is not a PerfRecord document."""


@dataclass(frozen=True)
class Compared:
    """One named duration on each side (None = absent on that side)."""

    name: str
    a: float | None
    b: float | None

    @property
    def delta(self) -> float | None:
        """A - B seconds, or None when either side lacks the value."""
        if self.a is None or self.b is None:
            return None
        return self.a - self.b

    @property
    def ratio(self) -> float | None:
        """A / B, or None when either side lacks the value or B is zero."""
        if self.a is None or self.b is None or self.b == 0:
            return None
        return self.a / self.b


@dataclass(frozen=True)
class PhaseDelta(Compared):
    """A phase's summed seconds on each side, plus each side's summed retries and
    failed-step count (#1215; 0 for a report that predates the ``failed`` key)."""

    a_retries: int | None = None
    b_retries: int | None = None
    a_failed: int | None = None
    b_failed: int | None = None


@dataclass(frozen=True)
class StealContributor:
    """One guest's mean vCPU steal % across the samples it appeared in."""

    guest: str
    mean_steal_pct: float
    samples: int


@dataclass
class DiffReport:
    """The A-vs-B comparison. `steal_a`/`steal_b` are None when that side has no samples."""

    a_stack: str | None
    b_stack: str | None
    phases: list[PhaseDelta]
    milestones: list[Compared]
    dominant: PhaseDelta | None
    steal_a: list[StealContributor] | None
    steal_b: list[StealContributor] | None
    host_a: dict[str, float | None] | None = None
    host_b: dict[str, float | None] | None = None
    a_notes: list[str] = field(default_factory=list)
    b_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self, *, label_a: str = "A", label_b: str = "B") -> str:
        """A plain-text table. Deltas are A - B; ratios are A / B. No verdict."""
        lines = [
            "Perf diff (delta = A - B, ratio = A / B; directional — different hardware)",
            f"  A = {label_a}  [{self.a_stack or '(unknown stack)'}]",
            f"  B = {label_b}  [{self.b_stack or '(unknown stack)'}]",
            "",
            f"  {'phase':<16} {'A s':>10} {'B s':>10} {'A-B s':>10} {'A/B':>8}  retries A/B",
        ]
        if not self.phases:
            lines.append("  (no phases)")
        for p in self.phases:
            retries = f"{_fmt_int(p.a_retries)}/{_fmt_int(p.b_retries)}"
            failed = (
                f"  FAILED steps A/B {_fmt_int(p.a_failed)}/{_fmt_int(p.b_failed)}"
                if p.a_failed or p.b_failed
                else ""
            )
            lines.append(f"  {_row(p.name or _UNPHASED, p)}  {retries}{failed}")
        lines += ["", f"  {'milestone':<16} {'A s':>10} {'B s':>10} {'A-B s':>10} {'A/B':>8}"]
        if not self.milestones:
            lines.append("  (no milestones)")
        lines.extend(f"  {_row(m.name, m)}" for m in self.milestones)
        lines.append("")
        if self.dominant is None:
            lines.append("Dominant divergence: none (no phase differs on both sides)")
        else:
            d = self.dominant
            lines.append(
                f"Dominant divergence: {d.name or _UNPHASED} "
                f"({_fmt_s(d.delta)}s, {_fmt_ratio(d.ratio)})"
            )
        lines += _steal_lines("A", self.steal_a) + _steal_lines("B", self.steal_b)
        lines += _host_lines("A", self.host_a) + _host_lines("B", self.host_b)
        for side, notes in (("A", self.a_notes), ("B", self.b_notes)):
            lines.extend(f"Report {side} note: {n}" for n in notes)
        lines.extend(f"Note: {n}" for n in self.notes)
        return "\n".join(lines)


def _fmt_s(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def _fmt_ratio(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}x"


def _fmt_int(v: int | None) -> str:
    return "—" if v is None else str(v)


def _row(name: str, c: Compared) -> str:
    return (
        f"{name:<16} {_fmt_s(c.a):>10} {_fmt_s(c.b):>10} "
        f"{_fmt_s(c.delta):>10} {_fmt_ratio(c.ratio):>8}"
    )


def _steal_lines(side: str, steal: list[StealContributor] | None) -> list[str]:
    if steal is None:
        return [f"{side} top steal contributors: n/a (no samples)"]
    if not steal:
        return [f"{side} top steal contributors: (no readable steal samples)"]
    body = ", ".join(f"{c.guest} {c.mean_steal_pct:.1f}% (n={c.samples})" for c in steal)
    return [f"{side} top steal contributors (mean steal %): {body}"]


def _host_lines(side: str, host: dict[str, float | None] | None) -> list[str]:
    if host is None:
        return [f"{side} host means: n/a (no samples)"]
    body = "  ".join(f"{k} {_fmt_pct(v)}" for k, v in host.items())
    return [f"{side} host means: {body}"]


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}%"


# --- validation of the PerfRecord JSON (fail loud) -------------------------------------


def _is_num(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _phases(rec: dict[str, Any], side: str) -> dict[str, tuple[float, int, int]]:
    """name -> (seconds, retries, failed). ``failed`` is optional (#1215: absent in a
    report that predates it -> 0) but, when present, must be an int."""
    raw = rec.get("phases")
    if not isinstance(raw, dict):
        msg = f"report {side}: 'phases' must be a mapping, got {raw!r}"
        raise PerfDiffError(msg)
    out: dict[str, tuple[float, int, int]] = {}
    for name, body in raw.items():
        ok = (
            isinstance(body, dict)
            and _is_num(body.get("seconds"))
            and _is_int(body.get("retries"))
            and _is_int(body.get("failed", 0))
        )
        if not ok:
            msg = (
                f"report {side}: phase {name!r} needs numeric 'seconds' + int 'retries' "
                "(+ int 'failed', if present)"
            )
            raise PerfDiffError(msg)
        out[name] = (float(body["seconds"]), int(body["retries"]), int(body.get("failed", 0)))
    return out


def _milestones(rec: dict[str, Any], side: str) -> dict[str, float]:
    raw = rec.get("milestones")
    if not isinstance(raw, dict):
        msg = f"report {side}: 'milestones' must be a mapping, got {raw!r}"
        raise PerfDiffError(msg)
    out: dict[str, float] = {}
    for name, seconds in raw.items():
        if not _is_num(seconds):
            msg = f"report {side}: milestone {name!r} must be numeric seconds, got {seconds!r}"
            raise PerfDiffError(msg)
        out[name] = float(seconds)
    return out


def _stack(rec: dict[str, Any], side: str) -> str | None:
    stack = rec.get("stack")
    if stack is not None and not isinstance(stack, str):
        msg = f"report {side}: 'stack' must be a string, got {stack!r}"
        raise PerfDiffError(msg)
    return stack


def _notes(rec: dict[str, Any], side: str) -> list[str]:
    notes = rec.get("notes", [])
    out = [n for n in notes if isinstance(n, str)] if isinstance(notes, list) else None
    if out is None or len(out) != len(notes):
        msg = f"report {side}: 'notes' must be a list of strings, got {notes!r}"
        raise PerfDiffError(msg)
    return out


# --- host-contention samples ------------------------------------------------------------
# ISOLATED + DEFENSIVE on purpose: the `samples` shape is owned by the sampler (#1203,
# `perf.HostContentionSample.as_dict`): a list of mappings, each with a `guests` mapping of
# guest name -> {"steal": pct}, where pct is null for a guest's baseline reading (no delta
# yet). A baseline is expected, so it is skipped without being counted. A guest value is
# also read as a bare number or a mapping holding `steal_pct`. Anything else is skipped and
# COUNTED into a note — never a crash, never silent.

_STEAL_KEYS = ("steal", "steal_pct")


def _guest_steal(value: object) -> float | None:
    if isinstance(value, dict):
        fields = cast("dict[str, object]", value)
        value = next((fields[k] for k in _STEAL_KEYS if _is_num(fields.get(k))), None)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _is_baseline(value: object) -> bool:
    """A sampler baseline reading: a steal key present with a null value (#1203)."""
    if not isinstance(value, dict):
        return False
    fields = cast("dict[str, object]", value)
    return any(k in fields and fields[k] is None for k in _STEAL_KEYS)


def _steal(rec: dict[str, Any], side: str) -> tuple[list[StealContributor] | None, list[str]]:
    if "samples" not in rec:
        return None, [
            f"{side}: no host-contention samples (report predates the sampler, #1203, "
            "or sampling was off) — steal contributors skipped"
        ]
    samples = rec["samples"]
    if not isinstance(samples, list):
        return None, [f"{side}: 'samples' is not a list ({type(samples).__name__}); skipped"]
    if not samples:
        return [], [f"{side}: 'samples' is empty"]
    per_guest: dict[str, list[float]] = {}
    unreadable = 0
    for sample in samples:
        guests = sample.get("guests") if isinstance(sample, dict) else None
        if not isinstance(guests, dict):
            unreadable += 1
            continue
        for guest, value in guests.items():
            steal = _guest_steal(value)
            if steal is None:
                unreadable += 0 if _is_baseline(value) else 1
            else:
                per_guest.setdefault(str(guest), []).append(steal)
    notes = []
    if unreadable:
        notes.append(f"{side}: {unreadable} sample entr(y/ies) had no readable steal; skipped")
    ranked = sorted(
        (StealContributor(g, sum(v) / len(v), len(v)) for g, v in per_guest.items()),
        key=lambda c: (-c.mean_steal_pct, c.guest),
    )
    return ranked[:TOP_STEAL], notes


# The host fields averaged per side (#1215): the lab host's CPU/iowait (virsh) and the
# sampling VM's own steal/busy/iowait (local /proc/stat). Same defensive stance as the
# steal contributors: a non-numeric/absent value is simply not a reading (the report's
# own notes say why a probe failed); a sample with no `host` mapping is counted.
_HOST_KEYS = ("cpu", "iowait", "self_steal", "self_busy", "self_iowait")


def _host_means(rec: dict[str, Any], side: str) -> tuple[dict[str, float | None] | None, list[str]]:
    samples = rec.get("samples")
    if not isinstance(samples, list) or not samples:
        return None, []  # absence/shape already noted by _steal
    readings: dict[str, list[float]] = {k: [] for k in _HOST_KEYS}
    unreadable = 0
    for sample in samples:
        host = sample.get("host") if isinstance(sample, dict) else None
        if not isinstance(host, dict):
            unreadable += 1
            continue
        for k in _HOST_KEYS:
            if _is_num(host.get(k)):
                readings[k].append(float(host[k]))
    notes = [f"{side}: {unreadable} sample(s) had no host mapping; skipped"] if unreadable else []
    means = {k: (sum(v) / len(v) if v else None) for k, v in readings.items()}
    return means, notes


# --- the diff ---------------------------------------------------------------------------


def _record(rec: object, side: str) -> dict[str, Any]:
    if not isinstance(rec, dict):
        msg = f"report {side}: expected a JSON object, got {type(rec).__name__}"
        raise PerfDiffError(msg)
    return cast("dict[str, Any]", rec)


def _union(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """A's keys in order, then B-only keys in B's order."""
    return list(a) + [k for k in b if k not in a]


def diff(a: dict[str, Any], b: dict[str, Any]) -> DiffReport:
    """Compare perf report A against B. Raises PerfDiffError on a malformed report."""
    ra, rb = _record(a, "A"), _record(b, "B")
    pa, pb = _phases(ra, "A"), _phases(rb, "B")
    ma, mb = _milestones(ra, "A"), _milestones(rb, "B")
    a_stack, b_stack = _stack(ra, "A"), _stack(rb, "B")

    phases = [
        PhaseDelta(
            name,
            pa[name][0] if name in pa else None,
            pb[name][0] if name in pb else None,
            pa[name][1] if name in pa else None,
            pb[name][1] if name in pb else None,
            pa[name][2] if name in pa else None,
            pb[name][2] if name in pb else None,
        )
        for name in _union(pa, pb)
    ]
    milestones = [Compared(name, ma.get(name), mb.get(name)) for name in _union(ma, mb)]

    diverging = [p for p in phases if p.delta]  # both sides present and a non-zero delta
    # max() keeps the first of equal |delta|s, so ties resolve to run order.
    dominant = max(diverging, key=lambda p: abs(p.delta or 0.0)) if diverging else None

    notes: list[str] = []
    if a_stack != b_stack:
        notes.append(f"stacks differ (A={a_stack}, B={b_stack}): the comparison crosses stacks")
    steal_a, na = _steal(ra, "A")
    steal_b, nb = _steal(rb, "B")
    notes += na + nb
    host_a, ha = _host_means(ra, "A")
    host_b, hb = _host_means(rb, "B")
    notes += ha + hb

    return DiffReport(
        a_stack=a_stack,
        b_stack=b_stack,
        phases=phases,
        milestones=milestones,
        dominant=dominant,
        steal_a=steal_a,
        steal_b=steal_b,
        host_a=host_a,
        host_b=host_b,
        a_notes=_notes(ra, "A"),
        b_notes=_notes(rb, "B"),
        notes=notes,
    )


def load(path: str | Path) -> dict[str, Any]:
    """Read one perf-*.json. Raises PerfDiffError (never returns a partial record)."""
    p = Path(path)
    try:
        text = p.read_text()
    except OSError as exc:
        msg = f"cannot read perf report {str(p)!r}: {exc.strerror or exc}"
        raise PerfDiffError(msg) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"perf report {str(p)!r} is not valid JSON: {exc}"
        raise PerfDiffError(msg) from exc
    if not isinstance(data, dict):
        msg = f"perf report {str(p)!r} must be a JSON object, got {type(data).__name__}"
        raise PerfDiffError(msg)
    return data
