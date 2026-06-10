"""The CommandRunner seam — the only code in mqlab that touches subprocess.

A verb's command steps execute through a CommandRunner. The real runner
(SubprocessRunner) spawns a process and streams its merged stdout/stderr line by
line to an on_line sink, returning the process exit code. Tests inject a
recording fake (tests/fakes.py) so the orchestrator core is exercised with no
live lab. This module is deliberately MQ-agnostic (spec §4.1, the liftable
nucleus).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True)
class Command:
    """One command to run: argv, an optional working directory, and optional
    extra environment (merged over os.environ for the child — used to inject
    lab secrets onto a single subprocess, #102)."""

    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None

    def display(self) -> str:
        """The verbatim, copy-pasteable command line (treatment A, spec §4.4)."""
        return " ".join(self.argv)


class CommandRunner(Protocol):
    """Runs a Command, streaming each output line to on_line; returns exit code."""

    def run(self, command: Command, on_line: Callable[[str], None]) -> int: ...


class SubprocessRunner:
    """Real runner: spawns the process, merges stdout+stderr, streams line by line."""

    def run(self, command: Command, on_line: Callable[[str], None]) -> int:
        cwd = str(command.cwd) if command.cwd is not None else None
        env = {**os.environ, **command.env} if command.env else None
        process = subprocess.Popen(  # noqa: S603 - trusted internal argv; lab tool (spec §1)
            command.argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        stream = process.stdout
        if stream is None:  # pragma: no cover - PIPE always yields a stream
            return process.wait()
        for line in stream:
            on_line(line.rstrip("\n"))
        return process.wait()
