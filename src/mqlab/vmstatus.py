"""`vm status` — topology-aware fleet view (#86).

Echoes the virsh command AND streams its raw output (the live-State source), tees
it to the transcript, then renders the *full* topology fleet joined with that state
— guests that exist and guests that are merely defined (`not created`), with stack and
box. Both inputs are exposed and the table caption attributes every column to
its source (#88): a transparency tool must show where its synthesis comes from. The
rendered table earns its keep because it composes two sources (vs the pass-through
net table removed in #70).
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


_CAPTION = (
    "Guest / Box / Stack(s) from lab/topology.yaml + lab/versions.yaml  ·  State from the virsh "
    "output above  "
    "( 'not created' = defined in topology, absent from virsh )"
)


def _table(rows: list[FleetRow]) -> Table:
    table = Table(title="lab fleet", caption=_CAPTION)
    for column in ("Guest", "Box", "State", "Stack(s)"):
        table.add_column(column)
    for row in rows:
        table.add_row(row.guest, row.box, row.state, row.stacks)
    return table


def vm_status_core(
    runner: CommandRunner,
    renderer: Renderer,
    transcript: Transcript,
    guests: list[str] | None = None,
) -> int:
    display = _VM_LIST.display()
    renderer.command(display)
    transcript.write(f"$ {display}")
    captured: list[str] = []

    def sink(line: str) -> None:
        # Stream the raw virsh output to the screen too — it is the live-State source,
        # and a transparency tool must show where the table's data comes from (#88).
        renderer.output(line)
        transcript.write(line)
        captured.append(line)

    exit_code = runner.run(_VM_LIST, sink)
    boxes = lab_guests()
    if guests is not None:
        boxes = {g: b for g, b in boxes.items() if g in guests}
    rows = fleet_rows(boxes, parse_domain_states("\n".join(captured)))
    renderer.table(_table(rows))
    return exit_code
