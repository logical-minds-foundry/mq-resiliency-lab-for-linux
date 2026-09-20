"""The run loop: execute a verb's command steps through the runner, render + tee,
honor --step. Fail loud on any non-zero exit (spec §4.1, principle 6)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.render import Renderer
    from mqlab.runner import Command, CommandRunner
    from mqlab.transcript import Transcript


@dataclass(frozen=True)
class RetryPolicy:
    """A bounded retry for a step that can fail *transiently* — e.g. a `vagrant up`
    batch whose base management-NIC DHCP lease times out (`Fog::Errors::TimeoutError`)
    when several heavy guests boot at once on a TCG-slow host (#1164).

    `attempts` is the TOTAL number of tries (>= 1); after the last one fails the run
    still halts loud (StepFailedError), so this is a bounded mitigation, never an
    unbounded loop that could mask a genuine boot/config error (spec §8). The wait
    before retry k (k = 1 for the first retry) is
    ``min(base_delay * backoff ** (k - 1), max_delay)`` seconds — generous enough to
    let a contended host settle, capped so the total added wall-clock stays bounded.

    Fails loud on a garbled policy (attempts < 1, or a negative delay/backoff) rather
    than silently clamping — the same no-silent-default stance as `phases._boot_batch`.
    This is data, not behaviour: `phases.py` declares it on a step (like its `ensure`
    tuples), while the retry LOOP lives here in the orchestrator, keeping the phase
    builders pure (spec §3, principle 7).
    """

    attempts: int = 3
    base_delay: float = 5.0
    backoff: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            msg = f"RetryPolicy.attempts must be >= 1, got {self.attempts!r}"
            raise ValueError(msg)
        if self.base_delay < 0 or self.backoff < 0 or self.max_delay < 0:
            msg = (
                "RetryPolicy base_delay/backoff/max_delay must be non-negative, got "
                f"{self.base_delay!r}/{self.backoff!r}/{self.max_delay!r}"
            )
            raise ValueError(msg)

    def delay_before(self, retry: int) -> float:
        """Seconds to wait before retry number `retry` (1-indexed), capped at max_delay."""
        return min(self.base_delay * self.backoff ** (retry - 1), self.max_delay)


@dataclass(frozen=True)
class CommandStep:
    """A step that runs one command, echoed verbatim then streamed.

    `retry` is an optional bounded RetryPolicy: when set, a non-zero exit is retried
    (with backoff) up to the policy's cap before the run fails loud. Default None is a
    single attempt — the pre-#1164 behaviour every non-boot step keeps.
    """

    label: str
    command: Command
    retry: RetryPolicy | None = None


class StepFailedError(RuntimeError):
    """A command step exited non-zero; the run halts loudly."""

    def __init__(self, label: str, exit_code: int) -> None:
        super().__init__(f"step {label!r} failed with exit {exit_code}")
        self.label = label
        self.exit_code = exit_code


class Pauser(Protocol):
    def wait(self) -> None: ...


def _run_step_with_retry(
    step: CommandStep,
    *,
    runner: CommandRunner,
    renderer: Renderer,
    transcript: Transcript,
    sink: Callable[[str], None],
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> float:
    """Run one step, retrying a transient non-zero exit per its RetryPolicy (bounded).

    Returns the successful attempt's elapsed seconds. A step with no policy is a single
    attempt (the pre-#1164 behaviour). When the exit stays non-zero after the last
    allowed attempt the run fails loud with StepFailedError — the retry is a
    transient-failure mitigation, never a mask for a genuine boot/config error (spec §8).
    """
    policy = step.retry
    attempts = policy.attempts if policy is not None else 1
    attempt = 0
    while True:
        attempt += 1
        started = now()
        exit_code = runner.run(step.command, sink)
        elapsed = now() - started
        if exit_code == 0:
            return elapsed
        exhausted = policy is None or attempt >= attempts
        if not exhausted:
            delay = policy.delay_before(attempt)  # type: ignore[union-attr]  # policy is not None here
            note = (
                f"{step.label}: attempt {attempt}/{attempts} failed "
                f"(exit {exit_code}); retrying in {delay:.0f}s"
            )
            renderer.note(note)
            transcript.write(f"RETRY: {note}")
            sleep(delay)
            continue
        renderer.fail(step.label, exit_code)
        transcript.write(f"FAILED: {step.label} exit {exit_code}")
        raise StepFailedError(step.label, exit_code)


def run_steps(
    steps: list[CommandStep],
    *,
    runner: CommandRunner,
    renderer: Renderer,
    transcript: Transcript,
    step_mode: bool,
    pauser: Pauser,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    total = len(steps)
    completed = 0
    elapsed_total = 0.0
    for index, step in enumerate(steps, start=1):
        display = step.command.display()
        renderer.command(display)
        transcript.write(f"$ {display}")

        def sink(line: str) -> None:
            renderer.output(line)
            transcript.write(line)

        elapsed = _run_step_with_retry(
            step,
            runner=runner,
            renderer=renderer,
            transcript=transcript,
            sink=sink,
            now=now,
            sleep=sleep,
        )
        renderer.ok(step.label, elapsed)
        transcript.write(f"OK: {step.label} {elapsed:.2f}s")
        completed += 1
        elapsed_total += elapsed
        if step_mode and index < total:
            pauser.wait()
    renderer.summary(completed, total, elapsed_total)
    transcript.write(f"SUMMARY: {completed}/{total} steps {elapsed_total:.2f}s")
