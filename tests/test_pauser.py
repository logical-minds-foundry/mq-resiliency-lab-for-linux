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
