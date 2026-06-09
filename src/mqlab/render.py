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
    from rich.table import Table


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


def summary_line(steps_ok: int, steps_total: int, seconds: float) -> Text:
    return Text.assemble(
        ("  ✓ ", "bold green"),
        (f"{steps_ok}/{steps_total} steps", "green"),
        (f"   {seconds:.2f}s", "grey50"),
    )


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

    def summary(self, steps_ok: int, steps_total: int, seconds: float) -> None:
        self._console.print(summary_line(steps_ok, steps_total, seconds))

    def table(self, table: Table) -> None:
        self._console.print(table)
