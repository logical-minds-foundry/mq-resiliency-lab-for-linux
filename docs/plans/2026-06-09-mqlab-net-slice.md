# `mqlab` Net Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mqlab` orchestrator core and its first vertical slice — `mqlab net up | down | status` — end to end, proving the pattern before any heavier arm.

**Architecture:** A thin Typer CLI delegates to a small orchestrator that runs a verb's *command steps* through a single `CommandRunner` seam (the only code touching `subprocess`), rendering each command verbatim (treatment A, Rich) and teeing everything to a `build/runs/` transcript. `net up`/`down` wrap the groomed, self-echoing `net-up.sh`/`net-down.sh`; `net status` reads `virsh net-list` directly. The runner seam is dependency-injected so the entire core is unit-tested with no live lab.

**Tech Stack:** Python 3.12, Typer (CLI), Rich (rendering), `pytest`/`pytest-cov`, `uv`, `vrg-validate`.

**Spec:** `docs/specs/2026-06-09-mqlab-orchestrator-design.md` (§4 architecture, §7 the net slice).

---

## Table of contents

- [File structure](#file-structure)
- [Conventions every task follows](#conventions-every-task-follows)
- [Task 1: Project setup — dependencies & console script](#task-1-project-setup--dependencies--console-script)
- [Task 2: The CommandRunner seam](#task-2-the-commandrunner-seam)
- [Task 3: Repo-root path anchors](#task-3-repo-root-path-anchors)
- [Task 4: Transcript — build/-only tee](#task-4-transcript--build-only-tee)
- [Task 5: Treatment-A renderer](#task-5-treatment-a-renderer)
- [Task 6: TTY pauser for --step](#task-6-tty-pauser-for---step)
- [Task 7: The orchestrator run loop](#task-7-the-orchestrator-run-loop)
- [Task 8: Groom the network scripts to self-echo](#task-8-groom-the-network-scripts-to-self-echo)
- [Task 9: CLI — net up / net down](#task-9-cli--net-up--net-down)
- [Task 10: CLI — net status](#task-10-cli--net-status)
- [Task 11: Update the site — getting-started net walkthrough](#task-11-update-the-site--getting-started-net-walkthrough)
- [Task 12: Full validation & lab-time smoke](#task-12-full-validation--lab-time-smoke)
- [Self-review](#self-review)

---

## File structure

| File | Responsibility |
|---|---|
| `src/mqlab/runner.py` | `Command` dataclass, `CommandRunner` protocol, `SubprocessRunner` (the only `subprocess` user). The liftable nucleus. |
| `src/mqlab/paths.py` | Repo-root anchor; `runs_dir()`, `lab_script()`. |
| `src/mqlab/transcript.py` | `Transcript` — tee lines to `build/runs/…`, refuse any path outside `build/`. |
| `src/mqlab/render.py` | Treatment-A Rich renderer + pure text builders. |
| `src/mqlab/pauser.py` | `TTYPauser` — `--step` pause via `/dev/tty`, fail fast headless. |
| `src/mqlab/orchestrator.py` | `CommandStep`, `StepFailed`, `run_steps()` — the run loop. |
| `src/mqlab/cli.py` | Typer app, `net` group, dependency wiring, `main()`. |
| `lab/scripts/net-up.sh` | Groomed to echo each `virsh` command (modify). |
| `lab/scripts/net-down.sh` | Groomed to echo each `virsh` command (modify). |
| `docs/site/docs/getting-started.md` | Rewrite the network section as the `mqlab net` walkthrough (modify). |
| `tests/fakes.py` | `RecordingRunner`, `ScriptedResult` — test doubles for the seam. |
| `tests/test_*.py` | One test module per source module. |

## Conventions every task follows

- **Run a single test:** `uv run pytest tests/test_X.py::test_name -v`
- **Commit:** `vrg-commit --type <type> --scope mqlab --message "<msg>"` (run from inside the worktree). End bodies with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer when a body is used.
- **Final gate (Task 11 only):** `vrg-container-run -- vrg-validate`.
- All modules start with `from __future__ import annotations`.
- Imports of typing-only names go under `if TYPE_CHECKING:` where ruff `TCH` asks; `Callable` comes from `collections.abc`.

---

## Task 1: Project setup — dependencies & console script

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add runtime deps and the console script**

In `pyproject.toml`, change the `dependencies` line to add Typer and Rich:

```toml
dependencies = ["pymqrest>=1.2,<2", "ansible-core>=2.16", "pyyaml>=6", "typer>=0.12", "rich>=13"]
```

Add a console-script entry point immediately after the `[project]` block's existing keys (a new table near the top, before `[dependency-groups]`):

```toml
[project.scripts]
mqlab = "mqlab.cli:main"
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: resolves and installs `typer` and `rich`; exit 0.

- [ ] **Step 3: Verify the package still imports**

Run: `uv run python -c "import mqlab; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
vrg-commit --type chore --scope mqlab --message "add typer + rich deps and the mqlab console script (#32)"
```

---

## Task 2: The CommandRunner seam

**Files:**
- Create: `src/mqlab/runner.py`
- Create: `tests/fakes.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:

```python
from __future__ import annotations

from mqlab.runner import Command, SubprocessRunner


def test_command_display_is_verbatim_argv():
    cmd = Command(["virsh", "net-start", "net-wan"])
    assert cmd.display() == "virsh net-start net-wan"


def test_subprocess_runner_streams_lines_and_returns_exit_code():
    lines: list[str] = []
    runner = SubprocessRunner()
    code = runner.run(Command(["sh", "-c", "echo one; echo two; exit 3"]), lines.append)
    assert lines == ["one", "two"]
    assert code == 3


def test_subprocess_runner_merges_stderr_into_the_stream():
    lines: list[str] = []
    runner = SubprocessRunner()
    code = runner.run(Command(["sh", "-c", "echo out; echo err 1>&2"]), lines.append)
    assert set(lines) == {"out", "err"}
    assert code == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.runner'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/runner.py`:

```python
"""The CommandRunner seam — the only code in mqlab that touches subprocess.

A verb's command steps execute through a CommandRunner. The real runner
(SubprocessRunner) spawns a process and streams its merged stdout/stderr line by
line to an on_line sink, returning the process exit code. Tests inject a
recording fake (tests/fakes.py) so the orchestrator core is exercised with no
live lab. This module is deliberately MQ-agnostic (spec §4.1, the liftable
nucleus).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True)
class Command:
    """One command to run: argv plus an optional working directory."""

    argv: list[str]
    cwd: Path | None = None

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
        process = subprocess.Popen(  # noqa: S603 - trusted internal argv; lab tool (spec §1)
            command.argv,
            cwd=cwd,
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
```

- [ ] **Step 4: Add the recording fake**

`tests/fakes.py`:

```python
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_runner.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "CommandRunner seam: Command, protocol, SubprocessRunner + recording fake (#32)"
```

---

## Task 3: Repo-root path anchors

**Files:**
- Create: `src/mqlab/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

`tests/test_paths.py`:

```python
from __future__ import annotations

from pathlib import Path

from mqlab.paths import lab_script, repo_root, runs_dir


def test_repo_root_contains_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_repo_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path


def test_runs_dir_is_under_build():
    assert runs_dir() == repo_root() / "build" / "runs"


def test_lab_script_points_at_lab_scripts_dir():
    assert lab_script("net-up.sh") == repo_root() / "lab" / "scripts" / "net-up.sh"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.paths'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/paths.py`:

```python
"""Filesystem anchors for the lab repo (spec §4.3, §7)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """The repo root — the directory holding pyproject.toml.

    Honors the MQLAB_REPO_ROOT override (set when mqlab runs outside an editable
    checkout); otherwise walks up from this file.
    """
    override = os.environ.get("MQLAB_REPO_ROOT")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repo root (pyproject.toml) not found above the mqlab package")  # pragma: no cover


def runs_dir() -> Path:
    """Where transcripts are written — always under the gitignored build/ tree."""
    return repo_root() / "build" / "runs"


def lab_script(name: str) -> Path:
    """Absolute path to a script under lab/scripts/."""
    return repo_root() / "lab" / "scripts" / name
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "repo-root path anchors: repo_root, runs_dir, lab_script (#32)"
```

---

## Task 4: Transcript — build/-only tee

**Files:**
- Create: `src/mqlab/transcript.py`
- Test: `tests/test_transcript.py`

- [ ] **Step 1: Write the failing test**

`tests/test_transcript.py`:

```python
from __future__ import annotations

import pytest

from mqlab.paths import runs_dir
from mqlab.transcript import Transcript, TranscriptError, transcript_path


def test_transcript_path_names_by_timestamp_and_verb():
    assert transcript_path("net-up", "20260609T101500Z") == runs_dir() / "20260609T101500Z-net-up.log"


def test_transcript_writes_lines_under_build(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    path = transcript_path("net-up", "20260609T101500Z")
    with Transcript(path) as t:
        t.write("$ virsh net-start net-wan")
        t.write("Network net-wan started")
    assert path.read_text(encoding="utf-8") == "$ virsh net-start net-wan\nNetwork net-wan started\n"


def test_transcript_refuses_paths_outside_build(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    with pytest.raises(TranscriptError):
        Transcript(tmp_path / "docs" / "reports" / "leak.log")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.transcript'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/transcript.py`:

```python
"""Run transcripts — tee everything to build/runs/, never outside build/ (spec §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mqlab.paths import runs_dir

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


class TranscriptError(RuntimeError):
    """A transcript path escaped the gitignored build/ tree."""


def transcript_path(verb: str, timestamp: str) -> Path:
    """build/runs/<timestamp>-<verb>.log."""
    return runs_dir() / f"{timestamp}-{verb}.log"


class Transcript:
    """A line tee that refuses any destination outside the build/ tree."""

    def __init__(self, path: Path) -> None:
        build_tree = runs_dir().parent  # .../build
        resolved = path.resolve()
        if build_tree not in resolved.parents:
            raise TranscriptError(f"transcript {resolved} is not under {build_tree}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = resolved
        self._fh = resolved.open("w", encoding="utf-8")

    def write(self, line: str) -> None:
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> Transcript:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "Transcript: build/-only line tee with a structural escape guard (#32)"
```

---

## Task 5: Treatment-A renderer

**Files:**
- Create: `src/mqlab/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
from __future__ import annotations

import io

from rich.console import Console

from mqlab.render import Renderer, command_line, fail_line, ok_line, output_line


def test_command_line_is_prefixed_and_verbatim():
    assert command_line("virsh net-start net-wan").plain == "  $ virsh net-start net-wan"


def test_output_line_is_indented():
    assert output_line("Network net-wan started").plain == "      Network net-wan started"


def test_ok_line_shows_label_and_elapsed():
    assert ok_line("networks up", 0.42).plain == "  ✓ networks up   0.42s"


def test_fail_line_shows_exit_code():
    assert fail_line("networks up", 3).plain == "  ✗ networks up   exit 3"


def test_renderer_prints_through_a_console():
    buffer = io.StringIO()
    renderer = Renderer(Console(file=buffer, force_terminal=False, width=80))
    renderer.command("virsh net-start net-wan")
    renderer.output("Network net-wan started")
    renderer.ok("networks up", 0.42)
    renderer.fail("networks up", 3)
    renderer.error("--step requires a terminal")
    text = buffer.getvalue()
    assert "$ virsh net-start net-wan" in text
    assert "Network net-wan started" in text
    assert "networks up" in text
    assert "exit 3" in text
    assert "--step requires a terminal" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.render'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/render.py`:

```python
"""Treatment-A annotated-transcript renderer (spec §4.4).

Pure text builders (testable in isolation) plus a thin Console wrapper. The
literal command is shown verbatim and copy-pasteable; raw output is dimmed
beneath it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console


def command_line(display: str) -> Text:
    return Text.assemble(("  $ ", "bold yellow"), (display, "yellow"))


def output_line(line: str) -> Text:
    return Text("      " + line, style="grey50")


def ok_line(label: str, seconds: float) -> Text:
    return Text.assemble(("  ✓ ", "bold green"), (label, "green"), (f"   {seconds:.2f}s", "grey50"))


def fail_line(label: str, exit_code: int) -> Text:
    return Text.assemble(("  ✗ ", "bold red"), (label, "red"), (f"   exit {exit_code}", "grey50"))


def error_line(message: str) -> Text:
    return Text.assemble(("  ! ", "bold red"), (message, "red"))


class Renderer:
    """Prints treatment-A lines through a Rich console."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def command(self, display: str) -> None:
        self._console.print(command_line(display))

    def output(self, line: str) -> None:
        self._console.print(output_line(line))

    def ok(self, label: str, seconds: float) -> None:
        self._console.print(ok_line(label, seconds))

    def fail(self, label: str, exit_code: int) -> None:
        self._console.print(fail_line(label, exit_code))

    def error(self, message: str) -> None:
        self._console.print(error_line(message))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "treatment-A Rich renderer: command/output/ok/fail/error lines (#32)"
```

---

## Task 6: TTY pauser for --step

**Files:**
- Create: `src/mqlab/pauser.py`
- Test: `tests/test_pauser.py`

- [ ] **Step 1: Write the failing test**

`tests/test_pauser.py`:

```python
from __future__ import annotations

import io

import pytest

from mqlab.pauser import NoTTYError, TTYPauser


def test_pauser_reads_one_line_from_the_tty():
    fake_tty = io.StringIO("\n")
    pauser = TTYPauser(open_tty=lambda: fake_tty)
    pauser.wait()  # returns after consuming the keypress
    assert fake_tty.tell() > 0


def test_pauser_fails_fast_without_a_tty():
    def no_tty():
        raise OSError("no /dev/tty")

    pauser = TTYPauser(open_tty=no_tty)
    with pytest.raises(NoTTYError):
        pauser.wait()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_pauser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.pauser'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/pauser.py`:

```python
"""Operator pause for --step: read a keypress from /dev/tty, fail fast headless.

Reading from /dev/tty (not stdin) means the pause survives the transcript tee and
never consumes piped input (spec §4.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO


class NoTTYError(RuntimeError):
    """--step was requested without an interactive terminal."""


class TTYPauser:
    """Pauses until the operator presses enter on the controlling terminal."""

    def __init__(self, open_tty: Callable[[], TextIO] | None = None) -> None:
        self._open_tty = open_tty or self._default_open

    @staticmethod
    def _default_open() -> TextIO:
        return Path("/dev/tty").open(encoding="utf-8")  # pragma: no cover - needs a real tty

    def wait(self) -> None:
        try:
            tty = self._open_tty()
        except OSError as exc:
            raise NoTTYError("--step requires an interactive terminal (no /dev/tty)") from exc
        with tty:
            tty.readline()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_pauser.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "TTYPauser: --step pause via /dev/tty, fail fast headless (#32)"
```

---

## Task 7: The orchestrator run loop

**Files:**
- Create: `src/mqlab/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

`tests/test_orchestrator.py`:

```python
from __future__ import annotations

import io

import pytest
from rich.console import Console

from mqlab.orchestrator import CommandStep, StepFailed, run_steps
from mqlab.render import Renderer
from mqlab.runner import Command
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class SpyPauser:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def _renderer() -> Renderer:
    return Renderer(Console(file=io.StringIO(), force_terminal=False, width=80))


def _transcript(tmp_path, monkeypatch) -> Transcript:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    return Transcript(transcript_path("test", "20260609T000000Z"))


def test_run_steps_runs_each_step_and_tees_to_transcript(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult(["Network net-wan started"])])
    transcript = _transcript(tmp_path, monkeypatch)
    clock = iter([10.0, 10.5])
    run_steps(
        [CommandStep("networks up", Command(["bash", "net-up.sh"]))],
        runner=runner,
        renderer=_renderer(),
        transcript=transcript,
        step_mode=False,
        pauser=SpyPauser(),
        now=lambda: next(clock),
    )
    transcript.close()
    body = transcript.path.read_text(encoding="utf-8")
    assert "$ bash net-up.sh" in body
    assert "Network net-wan started" in body
    assert "OK: networks up 0.50s" in body
    assert runner.recorded[0].argv == ["bash", "net-up.sh"]


def test_run_steps_raises_on_nonzero_exit(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=3)])
    transcript = _transcript(tmp_path, monkeypatch)
    with pytest.raises(StepFailed) as caught:
        run_steps(
            [CommandStep("networks up", Command(["bash", "net-up.sh"]))],
            runner=runner,
            renderer=_renderer(),
            transcript=transcript,
            step_mode=False,
            pauser=SpyPauser(),
            now=lambda: 0.0,
        )
    assert caught.value.exit_code == 3


def test_step_mode_pauses_between_steps_but_not_after_the_last(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    transcript = _transcript(tmp_path, monkeypatch)
    pauser = SpyPauser()
    run_steps(
        [
            CommandStep("a", Command(["true"])),
            CommandStep("b", Command(["true"])),
        ],
        runner=runner,
        renderer=_renderer(),
        transcript=transcript,
        step_mode=True,
        pauser=pauser,
        now=lambda: 0.0,
    )
    assert pauser.calls == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.orchestrator'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/orchestrator.py`:

```python
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


class StepFailed(RuntimeError):
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
            raise StepFailed(step.label, exit_code)
        renderer.ok(step.label, elapsed)
        transcript.write(f"OK: {step.label} {elapsed:.2f}s")
        if step_mode and index < total:
            pauser.wait()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "orchestrator run loop: command steps, fail-loud exits, --step pauses (#32)"
```

---

## Task 8: Groom the network scripts to self-echo

**Files:**
- Modify: `lab/scripts/net-up.sh`
- Modify: `lab/scripts/net-down.sh`

Goal: each `virsh` *action* is echoed verbatim before it runs (so `mqlab`'s treatment A surfaces the script's own commands), while idempotency and ordering are preserved. There is no Python unit test here — verification is `bash -n` syntax + lab-time functional run (Task 11).

- [ ] **Step 1: Groom `net-up.sh`**

Replace the body of `lab/scripts/net-up.sh` with:

```bash
#!/usr/bin/env bash
# lab/scripts/net-up.sh — define, start, autostart every lab network.
# Self-echoes each virsh action (run helper) so mqlab's treatment-A transcript
# shows the literal commands; idempotent (skip already-defined/active).
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }   # echo verbatim, then run
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-info "$net" >/dev/null 2>&1 \
    || run virsh -c qemu:///system net-define "$xml"
  # awk reads all input — avoids the pipefail+grep -q SIGPIPE footgun.
  active=$(virsh -c qemu:///system net-info "$net" | awk '/^Active:/{print $2}')
  [ "$active" = "yes" ] || run virsh -c qemu:///system net-start "$net"
  run virsh -c qemu:///system net-autostart "$net"
  echo "up: $net"
done
```

- [ ] **Step 2: Groom `net-down.sh`**

Replace the body of `lab/scripts/net-down.sh` with:

```bash
#!/usr/bin/env bash
# lab/scripts/net-down.sh — tear down every lab network (guests first!).
# Self-echoes each virsh action (run helper) for mqlab's treatment-A transcript.
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }
for xml in net-*.xml; do
  net="${xml%.xml}"
  run virsh -c qemu:///system net-destroy  "$net" 2>/dev/null || true
  run virsh -c qemu:///system net-undefine "$net" 2>/dev/null || true
  echo "down: $net"
done
```

- [ ] **Step 3: Syntax-check both scripts**

Run: `bash -n lab/scripts/net-up.sh && bash -n lab/scripts/net-down.sh && echo ok`
Expected: prints `ok` (no syntax errors).

- [ ] **Step 4: Commit**

```bash
vrg-commit --type refactor --scope lab --message "groom net-up/net-down scripts to self-echo each virsh action (#32)"
```

---

## Task 9: CLI — net up / net down

**Files:**
- Create: `src/mqlab/cli.py`
- Test: `tests/test_cli_net.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_net.py`:

```python
from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.orchestrator import StepFailed
from mqlab.pauser import NoTTYError
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


def _deps(runner, pauser, tmp_path):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("net-up", "20260609T000000Z")),
        pauser=pauser,
    )


def test_net_up_runs_the_groomed_script(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult(["up: net-wan"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause(), tmp_path))
    result = CliRunner().invoke(cli.app, ["net", "up"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[0] == "bash"
    assert runner.recorded[0].argv[1].endswith("lab/scripts/net-up.sh")


def test_net_up_propagates_step_failure_as_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause(), tmp_path))
    result = CliRunner().invoke(cli.app, ["net", "up"])
    assert result.exit_code == 3


def test_net_up_step_without_tty_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _FailPause(), tmp_path))
    # two steps so the pause between them fires; net up is one step, so use a stub verb
    monkeypatch.setattr(cli, "_net_up_steps", lambda: cli._twin_steps())
    result = CliRunner().invoke(cli.app, ["net", "up", "--step"])
    assert result.exit_code == 2


class _NoPause:
    def wait(self) -> None:
        return None


class _FailPause:
    def wait(self) -> None:
        raise NoTTYError("no tty")
```

> Note: `cli._net_up_steps` and `cli._twin_steps` exist to keep `net up` a single
> step in production while letting the headless-`--step` test exercise the pause
> branch. They are defined in Step 3.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_net.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.cli'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/cli.py`:

```python
"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from mqlab.orchestrator import CommandStep, StepFailed, run_steps
from mqlab.paths import lab_script
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.render import Renderer
from mqlab.runner import Command, SubprocessRunner
from mqlab.transcript import Transcript, transcript_path

if TYPE_CHECKING:
    from mqlab.orchestrator import Pauser
    from mqlab.runner import CommandRunner


@dataclass
class Deps:
    """The injectable dependencies of a run (real impls in build_deps)."""

    runner: CommandRunner
    renderer: Renderer
    transcript: Transcript
    pauser: Pauser


def build_deps(verb: str, timestamp: str) -> Deps:
    return Deps(
        runner=SubprocessRunner(),
        renderer=Renderer(Console()),
        transcript=Transcript(transcript_path(verb, timestamp)),
        pauser=TTYPauser(),
    )


def _execute(verb: str, steps: list[CommandStep], *, step_mode: bool) -> None:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps(verb, timestamp)
    try:
        run_steps(
            steps,
            runner=deps.runner,
            renderer=deps.renderer,
            transcript=deps.transcript,
            step_mode=step_mode,
            pauser=deps.pauser,
        )
    except NoTTYError as exc:
        deps.renderer.error(str(exc))
        raise typer.Exit(code=2) from exc
    except StepFailed as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()


def _net_up_steps() -> list[CommandStep]:
    return [CommandStep("networks up", Command(["bash", str(lab_script("net-up.sh"))]))]


def _net_down_steps() -> list[CommandStep]:
    return [CommandStep("networks down", Command(["bash", str(lab_script("net-down.sh"))]))]


def _twin_steps() -> list[CommandStep]:
    # Test-only helper: two trivial steps so the headless --step branch is reachable.
    return [
        CommandStep("a", Command(["true"])),
        CommandStep("b", Command(["true"])),
    ]


app = typer.Typer(help="mqlab — operator orchestrator for the MQ cluster lab", no_args_is_help=True)
net_app = typer.Typer(help="libvirt lab networks", no_args_is_help=True)
app.add_typer(net_app, name="net")

_StepFlag = Annotated[bool, typer.Option("--step", help="pause after each step to inspect the lab")]


@net_app.command("up")
def net_up(*, step: _StepFlag = False) -> None:
    """Define, start, and autostart every lab network."""
    _execute("net-up", _net_up_steps(), step_mode=step)


@net_app.command("down")
def net_down(*, step: _StepFlag = False) -> None:
    """Destroy and undefine every lab network."""
    _execute("net-down", _net_down_steps(), step_mode=step)


def main() -> None:
    app()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli_net.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "CLI net up/down: wrap groomed scripts, propagate failures + --step (#32)"
```

---

## Task 10: CLI — net status

**Files:**
- Modify: `src/mqlab/cli.py`
- Create: `src/mqlab/netstatus.py`
- Test: `tests/test_netstatus.py`

- [ ] **Step 1: Write the failing test**

`tests/test_netstatus.py`:

```python
from __future__ import annotations

from mqlab.netstatus import NetRow, net_status_core, parse_net_list
from tests.fakes import RecordingRunner, ScriptedResult

SAMPLE = """\
 Name              State    Autostart   Persistent
--------------------------------------------------------
 default           active   yes         yes
 net-wan           active   yes         yes
 net-san-b         inactive no          yes
"""


def test_parse_net_list_extracts_rows_and_skips_chrome():
    rows = parse_net_list(SAMPLE)
    assert rows == [
        NetRow("default", "active", "yes", "yes"),
        NetRow("net-wan", "active", "yes", "yes"),
        NetRow("net-san-b", "inactive", "no", "yes"),
    ]


def test_parse_net_list_ignores_blank_and_short_lines():
    assert parse_net_list("\n   \nName x\n----\n bad row\n") == []


def test_net_status_core_runs_virsh_and_returns_exit_code(capsys):
    runner = RecordingRunner(results=[ScriptedResult(SAMPLE.splitlines())])
    code = net_status_core(runner)
    assert code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "net-list", "--all"]
    out = capsys.readouterr().out
    assert "net-wan" in out
    assert "$ virsh -c qemu:///system net-list --all" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_netstatus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.netstatus'`.

- [ ] **Step 3: Write the implementation**

`src/mqlab/netstatus.py`:

```python
"""`mqlab net status` — a direct virsh read (no script exists), rendered as a table.

This is a check-style observe (spec §4.5): it shows the underlying virsh command,
then the parsed network states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from mqlab.render import command_line
from mqlab.runner import Command

if TYPE_CHECKING:
    from mqlab.runner import CommandRunner

_NET_LIST = Command(["virsh", "-c", "qemu:///system", "net-list", "--all"])  # noqa: S607 - virsh on PATH (lab)


@dataclass(frozen=True)
class NetRow:
    name: str
    state: str
    autostart: str
    persistent: str


def parse_net_list(text: str) -> list[NetRow]:
    rows: list[NetRow] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Name") or set(line) <= {"-"}:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        rows.append(NetRow(parts[0], parts[1], parts[2], parts[3]))
    return rows


def _table(rows: list[NetRow]) -> Table:
    table = Table(title="lab networks")
    for column in ("Name", "State", "Autostart", "Persistent"):
        table.add_column(column)
    for row in rows:
        table.add_row(row.name, row.state, row.autostart, row.persistent)
    return table


def net_status_core(runner: CommandRunner, console: Console | None = None) -> int:
    console = console or Console()
    console.print(command_line(_NET_LIST.display()))
    captured: list[str] = []
    exit_code = runner.run(_NET_LIST, captured.append)
    console.print(_table(parse_net_list("\n".join(captured))))
    return exit_code
```

- [ ] **Step 4: Wire the `status` command into the CLI**

In `src/mqlab/cli.py`, add the import near the other `mqlab` imports:

```python
from mqlab.netstatus import net_status_core
```

And add this command after `net_down` (before `def main`):

```python
@net_app.command("status")
def net_status() -> None:
    """Show which lab networks are defined / active / autostart."""
    code = net_status_core(SubprocessRunner())
    if code != 0:
        raise typer.Exit(code=code)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_netstatus.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "CLI net status: virsh net-list parse + Rich table (#32)"
```

---

## Task 11: Update the site — getting-started net walkthrough

**Files:**
- Modify: `docs/site/docs/getting-started.md`

The getting-started page describes network bring-up abstractly ("the harness
reads `topology.yaml` to create networks") and points at raw scripts. Now that
`mqlab net` exists, the network section becomes a concrete, watchable
walkthrough — the real entry point. **Scope is the network section only;** the
later sections (full-stack bring-up) describe verbs that don't exist yet, so they
stay until their slices land and the walkthrough grows from there.

- [ ] **Step 1: Replace the network section**

In `docs/site/docs/getting-started.md`, replace the entire section beginning
`## 2. Bring up the network fabric and a node set` (up to, but not including,
`## 3. Stand up one stack end to end`) with exactly:

````markdown
## 2. Drive the lab with `mqlab`

The lab is driven by **`mqlab`**, an operator orchestrator that does the
opposite of most tooling: rather than hiding the mechanics, it **shows** them.
Every command it runs — `virsh`, Ansible, `runmqsc` — is printed verbatim as it
runs, streamed live, and teed to a transcript under `build/runs/`. You can watch
a step, understand it, then reproduce it by hand. (Why expose rather than
encapsulate? Because the deliverable is transparent evidence for the
RDQM-vs-Ubuntu comparison — see [design & specs](design-and-specs.md).)

Bring up the libvirt network fabric and watch it happen:

```bash
mqlab net up        # define, start, autostart every lab network
mqlab net status    # which networks are defined / active / autostart
mqlab net down      # tear them all down
```

`mqlab net up` runs the proven `lab/scripts/net-up.sh`, echoing each
`virsh net-define` / `net-start` / `net-autostart` so you see — and can copy —
exactly what brings the fabric up. Add `--step` to pause after each step and go
poke at the live system; break something and `mqlab net down && mqlab net up` to
rebuild, because the lab is a disposable, reproducible illusion.

The lab's shape is a single source of truth:
[`lab/topology.yaml`](https://github.com/logical-minds-foundry/mq-cluster-tooling/blob/develop/lab/topology.yaml)
— the libvirt networks (data, heartbeat, WAN, client, DTCC, SAN) and every
guest's NICs and platform. See the [Architecture](architecture/index.md)
walkthrough for what each network is for.

> **More verbs land as the slices ship.** Today `mqlab net` is live; guest
> lifecycle (`mqlab vms`), arm setup, HA/DR operations, and the `status` /
> `check` dashboard arrive in subsequent slices, each extending this walkthrough.
````

- [ ] **Step 2: Stage and strict-build the site**

Run:
```bash
vrg-container-run -- vrg-docs-stage --docs-dir docs/site/docs
vrg-container-docs build --strict
```
Expected: the strict build succeeds with no broken links or orphan pages; the
internal links (`design-and-specs.md`, `architecture/index.md`) resolve.

- [ ] **Step 3: Commit**

```bash
vrg-commit --type docs --scope site --message "getting-started: mqlab net walkthrough replaces the abstract network section (#32)"
```

---

## Task 12: Full validation & lab-time smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full validation pipeline**

Run: `vrg-container-run -- vrg-validate`
Expected: green — ruff, mypy strict, and `pytest` with 100% branch coverage all pass.

- [ ] **Step 2: Fix any gate findings inline**

If ruff flags `S603`/`S607` anywhere beyond the annotated `# noqa` lines, add a justified `# noqa` (lab tool, trusted argv, spec §1). If coverage is below 100%, add the missing-branch test in the relevant `tests/test_*.py` and re-run Step 1. Do not add `pragma: no cover` except for the two already specified (the `repo_root` raise and `_default_open`).

- [ ] **Step 3: Lab-time functional smoke (run inside the lab VM, where libvirt exists)**

Run:
```bash
uv run mqlab net up
uv run mqlab net status
uv run mqlab net down
```
Expected: `net up` echoes each `virsh net-define/net-start/net-autostart` verbatim and ends with all lab networks up; `net status` shows them active; `net down` tears them down. A transcript for each appears under `build/runs/`. Confirm no transcript was written anywhere outside `build/`.

- [ ] **Step 4: Commit any fixes from Step 2**

```bash
vrg-commit --type test --scope mqlab --message "close coverage/lint gaps for the net slice (#32)"
```

---

## Self-review

**1. Spec coverage (§7 done-bar + §10 success criteria):**

- `net up`/`down` wrap groomed self-echoing scripts → Tasks 8, 9. ✅
- `net status` direct `virsh` read + render → Task 10. ✅
- Reusable core: step model + `CommandRunner` seam (T2, T7), renderer (T5), transcript tee (T4), run-through/`--step` loop (T6, T7), Typer skeleton (T9). ✅
- Criterion 2 (every command visible verbatim & reproducible): treatment A + self-echoing scripts → T5, T8. ✅
- Criterion 3 (transcript in `build/` only): T4 guard + T11 Step 3 check. ✅
- Criterion 4 (run-through non-interactive; `--step` `/dev/tty`, fail fast; gates both modes): T6, T7, T9. (Gate steps themselves are a later slice — none in `net`; the loop's pause/halt branches are covered.) ✅
- Criterion 5 (fail loud, non-zero propagates): T7 `StepFailed`, T9 `typer.Exit`. ✅
- Criterion 6 (thin surface): CLI delegates only; no abstraction beyond the verbs. ✅
- Criterion 7 (core unit-tested with no live lab): every src module tested via fakes/StringIO. ✅
- Criterion 8 (`vrg-validate` green): T12. ✅
- Site stays current: the getting-started network section becomes the `mqlab net`
  walkthrough, strict docs build verified → T11. (Later sections grow as their
  verbs ship.) ✅

**2. Placeholder scan:** No TBD/TODO; every code step contains complete code; every command has expected output. ✅

**3. Type/name consistency:** `Command.display()`, `CommandRunner.run(command, on_line)`, `RecordingRunner(results=, recorded=, index=)`, `ScriptedResult(lines=, exit_code=)`, `Transcript(path)`/`transcript_path(verb, timestamp)`, `Renderer.{command,output,ok,fail,error}`, `run_steps(steps, *, runner, renderer, transcript, step_mode, pauser, now)`, `StepFailed.exit_code`, `Deps(runner, renderer, transcript, pauser)`, `build_deps(verb, timestamp)`, `net_status_core(runner, console=None)`, `NetRow(name, state, autostart, persistent)` — names are used identically across tasks. ✅

**Note on the headless-`--step` test (T9):** `net up` is a single step in production (no pause fires), so the test swaps in `_twin_steps()` to make the pause branch reachable and assert the `NoTTYError → exit 2` path. The production single-step path and the multi-step pause path are both covered.
