"""Perf capture wired into `mqlab bootstrap` (#1205, epic #275 plan Task 3).

`BootstrapPerf` owns one bootstrap's `PerfRecord` and host-contention `Sampler`, and
turns what the run already produces into the report's milestones:

* **Per-VM boot** (`boot:<vm>`, `boot_retries:<vm>`) — from the vms phase's per-batch
  step timings. A batch is ONE `vagrant up <vm> <vm> ...` step that boots its VMs
  together, so per-guest time inside a batch is not separately measured: every VM is
  attributed its batch's successful-attempt wall-clock and that batch's retry count
  (failed attempts + backoff are excluded from the seconds, and show up as retries).
  The batch -> VMs map is read from the `vagrant up` argv actually executed.
* **Observe readiness** (`opensearch_green`, `data_prepper_ready`, `dashboards_ready`)
  — the three readiness waits are ansible TASKS inside the single `site-obs.yml` step,
  not orchestrator steps, so there is no step boundary for them. Their start -> success
  boundaries ARE in the run transcript: ansible.cfg enables the `profile_tasks`
  callback (#594), which prints a timing line at every task start carrying the
  PREVIOUS task's duration. Each milestone is its wait task's duration, parsed from
  there, and only when the task reported ok/changed.
* **`opensearch_bound` is a declared gap** — the OpenSearch wait polls
  `_cluster/health` until green|yellow in ONE task, so bind and green are not
  separately observable. It is noted in the record, never fabricated.
  (`opensearch_green` itself means green OR yellow — the wait's own healthy set.)
* **Pre-flight** (#1215) — the record and sampler start BEFORE the bootstrap's
  pre-flight (inventory render + `_probe_all` lab-state probe), and each pre-flight
  call is timed as a step of its own `preflight` phase via `BootstrapPerf.preflight`,
  so that time appears in the phase table rather than before `started_at`.

Additive and non-fatal, never silent (plan Global Constraints): every perf operation
is guarded; a failure is shown loudly through the renderer AND noted in the record,
and never raises into — or changes the exit status of — the bootstrap.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from mqlab import topology
from mqlab.paths import runs_dir
from mqlab.perf import PerfRecord
from mqlab.perfsampler import RealSource, Sampler

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping
    from pathlib import Path

    from mqlab.orchestrator import CommandStep
    from mqlab.perfsampler import SampleSource
    from mqlab.render import Renderer

_T = TypeVar("_T")

# Phase names as phases.py stamps them (duplicated as literals to keep this module free
# of the phases -> topology/stacks import chain; test_perfrun pins them to phases.PHASES).
_VMS = "vms"
_OBSERVE = "observe"
# The bootstrap pre-flight (#1215) is not a PHASES entry (nothing to resume from), but its
# time is reported as a phase of its own so it shows in the phase table.
PREFLIGHT = "preflight"


def prereq_phase(phase: str) -> str:
    """The report phase for a bootstrap phase's prerequisite ensures (#1248).

    Each selected phase ensures its declared prerequisites (boxes, MQ media, SAN debs,
    galaxy collections, PKI) before its own steps run. That time used to fall outside
    every report phase (about 240s of a cold run), so it is reported as
    `prereq:<phase>` (e.g. `prereq:vms`, which holds the box ensure), right before the
    phase it serves. Like `preflight`, it is a report phase only, never a resume point."""
    return f"prereq:{phase}"


# Readiness milestone -> the ansible task (role-prefixed, exactly as the TASK banner
# prints it) whose duration IS that milestone. test_perfrun pins each name to the role's
# tasks file, so renaming a task fails CI instead of silently dropping the milestone.
READINESS_TASKS: dict[str, str] = {
    "opensearch_green": "opensearch : wait for the OpenSearch cluster to accept requests",
    "data_prepper_ready": (
        "data-prepper : wait for the Data Prepper otel_logs_source to accept connections"
    ),
    "dashboards_ready": (
        "opensearch-dashboards : wait for OpenSearch Dashboards /api/status to report green"
    ),
}

OPENSEARCH_BOUND_GAP = (
    "opensearch_bound: not derivable — the OpenSearch readiness wait polls _cluster/health "
    "until green|yellow in a single ansible task, so bind and green are not separately "
    "observable; opensearch_green covers both"
)

BOOT_ATTRIBUTION = (
    "boot:<vm> = successful-attempt wall-clock of the `vagrant up` batch that booted the "
    "VM, shared by every VM in that batch (VMs in a batch boot together); failed attempts "
    "and backoff are excluded — see boot_retries:<vm>"
)

_TASK_BANNER = re.compile(r"^TASK \[(?P<name>.+)\] \*+\s*$")
# profile_tasks: `<date> (<prev task h:mm:ss.fff>)       <total h:mm:ss.fff> ****`. Only
# the tail is matched — the date part follows the controller's locale/datetime_format.
_TASK_TIMING = re.compile(
    r"\((?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})\)\s+\d+:\d{2}:\d{2}\.\d{3} \*+\s*$"
)


@dataclass
class TaskOutcome:
    """One ansible task as seen in a transcript: its duration (None = it never finished
    — the run ended mid-task) and whether any host reported ok/changed or fatal."""

    seconds: float | None = None
    ok: bool = False
    failed: bool = False


def _timing_seconds(m: re.Match[str]) -> float:
    return int(m["h"]) * 3600 + int(m["m"]) * 60 + int(m["s"]) + int(m["ms"]) / 1000


def ansible_task_outcomes(lines: Iterable[str], names: Collection[str]) -> dict[str, TaskOutcome]:
    """The first run of each named ansible task in `lines` (profile_tasks output).

    A task starts at the timing line right after its TASK banner and ends at the next
    timing line (the next task's, a handler's, or the TASKS RECAP), whose parenthesised
    value is the task's duration. A named task absent from the result never started.
    """
    out: dict[str, TaskOutcome] = {}
    banner: str | None = None  # the latest TASK banner, until its start-timing line
    active: str | None = None  # the named task currently running
    for line in lines:
        if m := _TASK_BANNER.match(line):
            banner = m["name"]
            continue
        if t := _TASK_TIMING.search(line):
            if active is not None:
                out[active].seconds = _timing_seconds(t)
                active = None
            if banner is not None and banner in names and banner not in out:
                out[banner] = TaskOutcome()
                active = banner
            banner = None
            continue
        if active is not None:
            if line.startswith(("ok: [", "changed: [")):
                out[active].ok = True
            elif line.startswith("fatal: ["):
                out[active].failed = True
    return out


def vagrant_up_batches(steps: Iterable[CommandStep]) -> dict[str, list[str]]:
    """Step label -> the VMs its `vagrant up` boots (argv after `up`, flags dropped)."""
    batches: dict[str, list[str]] = {}
    for step in steps:
        argv = step.command.argv
        if argv[:2] == ["vagrant", "up"]:
            batches[step.label] = [a for a in argv[2:] if not a.startswith("-")]
    return batches


def report_path(timestamp: str) -> Path:
    """`<runs dir>/perf-<ts>.json` — beside the bootstrap's `<ts>-bootstrap.log`
    transcript, in the shared state bucket (`$(mqlab build path state)/runs/`), resolved
    through the build-path API (never a hardcoded build/<X>)."""
    return runs_dir() / f"perf-{timestamp}.json"


def default_source(topo: Mapping[str, Any]) -> SampleSource:
    """The lab sample source (the seam the test suite replaces with a fake)."""
    return RealSource.from_topology(topo)


class BootstrapPerf:
    """One bootstrap's perf capture. Every public method is non-fatal (see module doc)."""

    def __init__(self, record: PerfRecord, renderer: Renderer) -> None:
        self.record = record
        self._renderer = renderer
        self._sampler: Sampler | None = None
        self._batches: dict[str, list[str]] = {}
        self._observe_ran = False
        self._outcome: str | None = None

    @classmethod
    def start(
        cls,
        stack: str,
        guests: Callable[[], list[str]],
        *,
        renderer: Renderer,
        clock: Callable[[], float] = time.time,
        interval: float = 15.0,
    ) -> BootstrapPerf:
        """Create the record (started_at = now, the sampler's t origin) and start the
        sampler over `guests()`. A sampler that cannot start is noted; timings still flow."""
        perf = cls(PerfRecord(stack=stack, started_at=clock()), renderer)
        try:
            source = default_source(topology.load())
            sampler = Sampler(perf.record, source, guests(), interval, clock=clock)
            sampler.start()
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly below
            perf._degraded("host-contention sampler not started", exc)
        else:
            perf._sampler = sampler
        return perf

    def _degraded(self, what: str, exc: BaseException) -> None:
        msg = f"{what} ({type(exc).__name__}: {exc})"
        self.record.note(f"perf: {msg}")
        self._renderer.error(f"perf: WARNING — {msg}; the bootstrap itself is unaffected")

    def register_phase(self, phase: str, steps: list[CommandStep]) -> None:
        """Note what a phase is about to run (the vms batch map; that observe ran)."""
        try:
            if phase == _VMS:
                self._batches.update(vagrant_up_batches(steps))
            elif phase == _OBSERVE:
                self._observe_ran = True
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
            self._degraded(f"could not register the {phase} phase", exc)

    def preflight(
        self, label: str, fn: Callable[[], _T], *, now: Callable[[], float] = time.monotonic
    ) -> _T:
        """Run one pre-flight call, timing it as a `preflight` step (#1215)."""
        return self.timed(PREFLIGHT, label, fn, now=now)

    def timed(
        self,
        phase: str,
        label: str,
        fn: Callable[[], _T],
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> _T:
        """Run one in-process call, timing it as a step of `phase` (#1215, #1248).

        `fn`'s result and exceptions pass through untouched — timing never changes the
        run. A call that raises is still recorded (ok=False) with its elapsed time.
        """
        started = now()
        try:
            result = fn()
        except BaseException:
            self._timed_step(phase, label, now() - started, ok=False)
            raise
        self._timed_step(phase, label, now() - started, ok=True)
        return result

    def _timed_step(self, phase: str, label: str, seconds: float, *, ok: bool) -> None:
        try:
            self.record.add_step(phase, label, seconds, 0, ok=ok)
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
            self._degraded(f"{phase} step {label!r} not timed", exc)

    def nothing_to_do(self) -> None:
        """Every phase was already satisfied — the report holds just the pre-flight."""
        self._outcome = "bootstrap: already satisfied — nothing to do"

    def failed(self, phase: str, exit_code: int) -> None:
        """The bootstrap halted on a failed step in `phase` — say so in the report."""
        self._outcome = f"bootstrap FAILED in phase {phase} (exit {exit_code})"

    def completed(self) -> None:
        self._outcome = "bootstrap completed"

    def finish(self, transcript: Path, timestamp: str) -> None:
        """Stop sampling, derive the milestones, print the summary and write the report
        to `report_path(timestamp)`. Runs in the bootstrap's `finally`, so a failed
        bootstrap still gets its report."""
        self.record.note(
            self._outcome or "bootstrap did not complete (stopped outside a step; see transcript)"
        )
        if self._sampler is not None:
            try:
                self._sampler.stop()
            except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
                self._degraded("host-contention sampler did not stop cleanly", exc)
        try:
            self._boot_milestones()
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
            self._degraded("per-VM boot milestones unavailable", exc)
        if self._observe_ran:
            try:
                self._readiness_milestones(transcript)
            except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
                self._degraded("observe readiness milestones unavailable", exc)
        self._emit(timestamp)

    def _boot_milestones(self) -> None:
        if not self._batches:
            return
        timed: set[str] = set()
        for s in self.record.steps:
            # A failed batch (#1215: recorded, ok=False) booted nothing to attribute.
            if s.phase != _VMS or s.label not in self._batches or not s.ok:
                continue
            timed.add(s.label)
            for vm in self._batches[s.label]:
                self.record.add_milestone(f"boot:{vm}", s.seconds)
                self.record.add_milestone(f"boot_retries:{vm}", s.retries)
        if timed:
            self.record.note(BOOT_ATTRIBUTION)
        untimed = [vm for label, vms in self._batches.items() if label not in timed for vm in vms]
        if untimed:
            self.record.note(
                f"boot: no timing for {', '.join(untimed)} (their vms batch did not complete)"
            )

    def _readiness_milestones(self, transcript: Path) -> None:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
        outcomes = ansible_task_outcomes(lines, READINESS_TASKS.values())
        self.record.note(OPENSEARCH_BOUND_GAP)
        for milestone, task in READINESS_TASKS.items():
            o = outcomes.get(task)
            if o is None:
                self.record.note(f"{milestone}: not observed — task {task!r} never started")
            elif o.seconds is None:
                self.record.note(f"{milestone}: not observed — {task!r} never finished")
            elif o.failed or not o.ok:
                self.record.note(
                    f"{milestone}: not reached — {task!r} did not succeed ({o.seconds:.2f}s)"
                )
            else:
                self.record.add_milestone(milestone, o.seconds)

    def _emit(self, timestamp: str) -> None:
        try:
            summary = self.record.human_summary()
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
            self._degraded("perf summary unavailable", exc)
        else:
            for line in summary.splitlines():
                self._renderer.output(line)
        try:
            out = report_path(timestamp)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(self.record.to_json() + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - perf is non-fatal; surfaced loudly
            self._degraded(f"perf report perf-{timestamp}.json NOT written", exc)
        else:
            self._renderer.note(f"perf report -> {out}")
