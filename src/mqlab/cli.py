"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from mqlab.netstatus import net_status_core
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
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
    except StepFailedError as exc:
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
def net_up(step: _StepFlag = False) -> None:
    """Define, start, and autostart every lab network."""
    _execute("net-up", _net_up_steps(), step_mode=step)


@net_app.command("down")
def net_down(step: _StepFlag = False) -> None:
    """Destroy and undefine every lab network."""
    _execute("net-down", _net_down_steps(), step_mode=step)


@net_app.command("status")
def net_status() -> None:
    """Show which lab networks are defined / active / autostart."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("net-status", timestamp)
    try:
        code = net_status_core(deps.runner, deps.renderer, deps.transcript)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


def main() -> None:
    app()
