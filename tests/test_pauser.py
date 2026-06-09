from __future__ import annotations

import pytest

from mqlab.pauser import NoTTYError, TTYPauser


class _FakeTTY:
    """A fake controlling terminal that records the read and ignores close."""

    def __init__(self) -> None:
        self.read = False

    def readline(self) -> str:
        self.read = True
        return "\n"

    def __enter__(self) -> _FakeTTY:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_pauser_reads_one_line_from_the_tty():
    tty = _FakeTTY()
    TTYPauser(open_tty=lambda: tty).wait()
    assert tty.read is True


def test_pauser_fails_fast_without_a_tty():
    def no_tty():
        raise OSError("no /dev/tty")

    pauser = TTYPauser(open_tty=no_tty)
    with pytest.raises(NoTTYError):
        pauser.wait()
