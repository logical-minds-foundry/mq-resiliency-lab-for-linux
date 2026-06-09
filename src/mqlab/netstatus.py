"""`mqlab net status` — a direct virsh read (no script exists), rendered as a table.

This is a check-style observe (spec §4.5): it echoes the underlying virsh command,
streams the raw output (treatment A) and tees it, then renders the parsed states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.table import Table

from mqlab.runner import Command

if TYPE_CHECKING:
    from mqlab.render import Renderer
    from mqlab.runner import CommandRunner
    from mqlab.transcript import Transcript

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


def net_status_core(runner: CommandRunner, renderer: Renderer, transcript: Transcript) -> int:
    display = _NET_LIST.display()
    renderer.command(display)
    transcript.write(f"$ {display}")
    captured: list[str] = []

    def sink(line: str) -> None:
        renderer.output(line)
        transcript.write(line)
        captured.append(line)

    exit_code = runner.run(_NET_LIST, sink)
    renderer.table(_table(parse_net_list("\n".join(captured))))
    return exit_code
