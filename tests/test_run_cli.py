from __future__ import annotations

import pytest
import typer

from mqlab.cli import _lookup_setup_or_exit


def test_lookup_setup_returns_known_setup() -> None:
    setup = _lookup_setup_or_exit("distributed")
    assert setup.name == "distributed"


def test_lookup_setup_unknown_exits() -> None:
    with pytest.raises(typer.Exit) as exc:
        _lookup_setup_or_exit("nope-not-a-setup")
    assert exc.value.exit_code == 2
