"""mqlab — the operator orchestrator CLI (Typer + Rich). A thin veneer (spec §2)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from mqlab.guestsel import resolve_guests
from mqlab.netsel import resolve_nets
from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.paths import lab_script, repo_root
from mqlab.pauser import NoTTYError, TTYPauser
from mqlab.render import Renderer
from mqlab.runner import Command, SubprocessRunner
from mqlab.transcript import Transcript, transcript_path
from mqlab.vmstatus import vm_status_core

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _resolve_or_exit(pattern: str, resolver: Callable[[str], list[str]], noun: str) -> list[str]:
    """Resolve a selection pattern to names, or exit 2 with a clear message (#75)."""
    try:
        names = resolver(pattern)
    except re.error as exc:
        typer.echo(f"invalid pattern /{pattern}/: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not names:
        typer.echo(f"no lab {noun} matches /{pattern}/", err=True)
        raise typer.Exit(code=2)
    return names


def _net_up_steps(nets: list[str]) -> list[CommandStep]:
    return [CommandStep("networks up", Command(["bash", str(lab_script("net-up.sh")), *nets]))]


def _net_down_steps(nets: list[str]) -> list[CommandStep]:
    return [CommandStep("networks down", Command(["bash", str(lab_script("net-down.sh")), *nets]))]


_NET_LIST = Command(["virsh", "-c", "qemu:///system", "net-list", "--all"])  # noqa: S607 - virsh on PATH (lab)


def _net_status_steps() -> list[CommandStep]:
    # No script exists; mqlab drives virsh directly. virsh's own output is already
    # tabular, so this is a plain treatment-A pass-through — no re-rendering (#70).
    return [CommandStep("networks status", _NET_LIST)]


def _net_show_steps(nets: list[str]) -> list[CommandStep]:
    # Multi-step verb (#75): three virsh reads per selected net — config and who is
    # attached. Pass-through; net-dhcp-leases exits 0 even on no-DHCP nets.
    base = ["virsh", "-c", "qemu:///system"]
    reads = [("info", "net-info"), ("config", "net-dumpxml"), ("leases", "net-dhcp-leases")]
    return [
        CommandStep(f"{net} {label}", Command([*base, verb, net]))  # noqa: S607
        for net in nets
        for label, verb in reads
    ]


app = typer.Typer(help="mqlab — operator orchestrator for the MQ cluster lab", no_args_is_help=True)
net_app = typer.Typer(help="libvirt lab networks", no_args_is_help=True)
app.add_typer(net_app, name="net")

_StepFlag = Annotated[bool, typer.Option("--step", help="pause after each step to inspect the lab")]
_Pattern = Annotated[str, typer.Argument(help="name, regex, or 'all'")]


@net_app.command("up")
def net_up(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Define/start/autostart the selected lab networks (a name, regex, or 'all')."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute("net-up", _net_up_steps(nets), step_mode=step)


@net_app.command("down")
def net_down(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Destroy/undefine the selected lab networks (a name, regex, or 'all')."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute("net-down", _net_down_steps(nets), step_mode=step)


@net_app.command("status")
def net_status() -> None:
    """Show which lab networks are defined / active / autostart."""
    _execute("net-status", _net_status_steps(), step_mode=False)


@net_app.command("show")
def net_show(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Show config + DHCP leases for the selected lab networks (name, regex, or 'all')."""
    nets = _resolve_or_exit(pattern, resolve_nets, "network")
    _execute("net-show", _net_show_steps(nets), step_mode=step)


vm_app = typer.Typer(help="lab guest VMs (vagrant + virsh)", no_args_is_help=True)
app.add_typer(vm_app, name="vm")


def _vm_up_steps(guests: list[str]) -> list[CommandStep]:
    lab = repo_root() / "lab"
    return [
        CommandStep(f"{g} up", Command(["vagrant", "up", g], cwd=lab))  # noqa: S607
        for g in guests
    ]


def _vm_down_steps(guests: list[str]) -> list[CommandStep]:
    lab = repo_root() / "lab"
    return [
        CommandStep(f"{g} halt", Command(["vagrant", "halt", g], cwd=lab))  # noqa: S607
        for g in guests
    ]


def _vm_destroy_steps(guests: list[str]) -> list[CommandStep]:
    lab = repo_root() / "lab"
    return [
        CommandStep(f"{g} destroy", Command(["vagrant", "destroy", "-f", g], cwd=lab))  # noqa: S607
        for g in guests
    ]


def _ssh_into(guest: str) -> None:
    # Interactive: replace this process with vagrant ssh so the TTY passes through —
    # the one verb that is not a captured/streamed step. Runs from lab/.
    os.chdir(repo_root() / "lab")
    os.execvp("vagrant", ["vagrant", "ssh", guest])  # noqa: S606, S607 - TTY passthrough (lab)


@vm_app.command("up")
def vm_up(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Boot and provision the selected guests (a name, regex, or 'all')."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute("vm-up", _vm_up_steps(guests), step_mode=step)


@vm_app.command("down")
def vm_down(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Halt the selected guests (a name, regex, or 'all')."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute("vm-down", _vm_down_steps(guests), step_mode=step)


@vm_app.command("destroy")
def vm_destroy(pattern: _Pattern, step: _StepFlag = False) -> None:
    """Destroy (remove) the selected guests (a name, regex, or 'all')."""
    guests = _resolve_or_exit(pattern, resolve_guests, "guest")
    _execute("vm-destroy", _vm_destroy_steps(guests), step_mode=step)


@vm_app.command("status")
def vm_status(selector: _Pattern = "all") -> None:
    """Show the fleet — topology joined with live virsh state; optional selector filters it."""
    guests = _resolve_or_exit(selector, resolve_guests, "guest")
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    deps = build_deps("vm-status", timestamp)
    try:
        code = vm_status_core(deps.runner, deps.renderer, deps.transcript, guests=guests)
    finally:
        deps.transcript.close()
    if code != 0:
        raise typer.Exit(code=code)


@vm_app.command("ssh")
def vm_ssh(guest: str) -> None:
    """Open an interactive shell on one guest (vagrant ssh)."""
    _ssh_into(guest)


def main() -> None:
    app()
