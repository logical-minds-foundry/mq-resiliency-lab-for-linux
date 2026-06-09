from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import pytest

from mqlab.pauser import NoTTYError, TTYPauser

if TYPE_CHECKING:
    from typing import TextIO


def test_pauser_reads_one_line_from_the_tty():
    tty = cast("TextIO", io.StringIO("\n"))
    assert TTYPauser(open_tty=lambda: tty).wait() is None


def test_pauser_fails_fast_without_a_tty():
    def no_tty():
        raise OSError("no /dev/tty")

    pauser = TTYPauser(open_tty=no_tty)
    with pytest.raises(NoTTYError):
        pauser.wait()
