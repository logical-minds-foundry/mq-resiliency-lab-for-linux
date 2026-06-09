"""`vm status` — topology-aware fleet view (#86).

Echoes the virsh command (transparency) and tees its raw rows to the transcript,
then renders the *full* topology fleet joined with live virsh state — guests that
exist and guests that are merely defined (`not created`), with arm and platform.
The rendered table earns its keep here because it composes two sources (vs the
pass-through net table removed in #70).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from mqlab.fleet import fleet_rows, lab_guests, parse_domain_states
from mqlab.runner import Command

if TYPE_CHECKING:
    from mqlab.fleet import FleetRow
    from mqlab.render import Renderer
    from mqlab.runner import CommandRunner
    from mqlab.transcript import Transcript

_VM_LIST = Command(["virsh", "-c", "qemu:///system", "list", "--all"])  # noqa: S607 - virsh on PATH (lab)


def _table(rows: list[FleetRow]) -> Table:
    table = Table(title="lab fleet")
    for column in ("Guest", "Arm", "Platform", "State"):
        table.add_column(column)
    for row in rows:
        table.add_row(row.guest, row.arm, row.platform, row.state)
    return table


def vm_status_core(runner: CommandRunner, renderer: Renderer, transcript: Transcript) -> int:
    display = _VM_LIST.display()
    renderer.command(display)
    transcript.write(f"$ {display}")
    captured: list[str] = []

    def sink(line: str) -> None:
        # Raw rows go to the transcript (evidence); the screen gets the joined table.
        transcript.write(line)
        captured.append(line)

    exit_code = runner.run(_VM_LIST, sink)
    rows = fleet_rows(lab_guests(), parse_domain_states("\n".join(captured)))
    renderer.table(_table(rows))
    return exit_code
