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
class CommandStep:
    """A step that runs one command, echoed verbatim then streamed."""

    label: str
    command: Command


class StepFailedError(RuntimeError):
    """A command step exited non-zero; the run halts loudly."""

    def __init__(self, label: str, exit_code: int) -> None:
        super().__init__(f"step {label!r} failed with exit {exit_code}")
        self.label = label
        self.exit_code = exit_code


class Pauser(Protocol):
    def wait(self) -> None: ...


def run_steps(
    steps: list[CommandStep],
    *,
    runner: CommandRunner,
    renderer: Renderer,
    transcript: Transcript,
    step_mode: bool,
    pauser: Pauser,
    now: Callable[[], float] = time.monotonic,
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

        started = now()
        exit_code = runner.run(step.command, sink)
        elapsed = now() - started
        if exit_code != 0:
            renderer.fail(step.label, exit_code)
            transcript.write(f"FAILED: {step.label} exit {exit_code}")
            raise StepFailedError(step.label, exit_code)
        renderer.ok(step.label, elapsed)
        transcript.write(f"OK: {step.label} {elapsed:.2f}s")
        completed += 1
        elapsed_total += elapsed
        if step_mode and index < total:
            pauser.wait()
    renderer.summary(completed, total, elapsed_total)
    transcript.write(f"SUMMARY: {completed}/{total} steps {elapsed_total:.2f}s")
