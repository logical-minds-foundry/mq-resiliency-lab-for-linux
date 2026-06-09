"""Test doubles for the CommandRunner seam (spec §4.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.runner import Command


@dataclass
class ScriptedResult:
    """The output lines and exit code a RecordingRunner replays for one call."""

    lines: list[str]
    exit_code: int = 0


@dataclass
class RecordingRunner:
    """Replays scripted output per call and records the commands it ran."""

    results: list[ScriptedResult] = field(default_factory=list)
    recorded: list[Command] = field(default_factory=list)
    index: int = 0

    def run(self, command: Command, on_line: Callable[[str], None]) -> int:
        self.recorded.append(command)
        result = self.results[self.index]
        self.index += 1
        for line in result.lines:
            on_line(line)
        return result.exit_code
