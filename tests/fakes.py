"""Test doubles for the CommandRunner seam (spec §4.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mqlab.perfsampler import GuestSample, HostSample, LocalSample

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mqlab.runner import Command


@dataclass
class ScriptedResult:
    """The output lines and exit code a RecordingRunner replays for one call."""

    lines: Sequence[str]
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


@dataclass
class FakeSampleSource:
    """A perf SampleSource (#1205) with canned readings and no live calls (no virsh/ssh)."""

    probed: list[str] = field(default_factory=list)

    def host(self) -> HostSample:
        return HostSample(cpu_pct=10.0, iowait_pct=1.0, cpus=24)

    def local(self) -> LocalSample:
        return LocalSample(steal_pct=0.5, busy_pct=20.0, iowait_pct=0.25)

    def guest(self, name: str) -> GuestSample:
        self.probed.append(name)
        return GuestSample(steal_pct=2.5)

    def notes(self) -> list[str]:
        return []

    def close(self) -> list[str]:
        return []
