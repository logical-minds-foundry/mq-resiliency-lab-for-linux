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
