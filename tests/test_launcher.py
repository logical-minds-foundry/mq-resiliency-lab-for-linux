"""The console-script launcher and its stale-venv bootstrap guard (#1063).

The launcher is the entry point precisely because the in-CLI `uv sync` self-heal
is unreachable when the venv is too stale to import the package. These tests
drive the guard through injected seams so no real interpreter mismatch is
needed: a SyntaxError under a too-old interpreter must become remediation, while
the same error under a satisfying interpreter must stay a raw (real-bug) failure.
"""

from __future__ import annotations

import pytest

from mqlab import launcher


def test_min_python_matches_the_pin() -> None:
    assert launcher.MIN_PYTHON == (3, 14)


def test_happy_path_invokes_cli_main() -> None:
    calls: list[str] = []
    launcher.main(import_cli_main=lambda: lambda: calls.append("ran"))
    assert calls == ["ran"]


def test_default_import_seam_returns_cli_main() -> None:
    # Exercises the real import seam (the default arg) so it is covered and shown
    # to resolve to the CLI's own main.
    from mqlab import cli

    assert launcher._import_cli_main() is cli.main


def test_stale_venv_surfaces_remediation_and_exits() -> None:
    written: list[str] = []

    def _boom():
        raise SyntaxError("invalid syntax")

    with pytest.raises(SystemExit) as excinfo:
        launcher.main(import_cli_main=_boom, version_info=(3, 12, 7), stderr=written.append)

    assert excinfo.value.code == 1
    message = "".join(written)
    assert "stale" in message
    assert "uv sync" in message
    assert "3.12.7" in message  # names the running (too-old) interpreter


def test_genuine_syntaxerror_on_current_interpreter_is_reraised() -> None:
    written: list[str] = []

    def _boom():
        raise SyntaxError("a real bug in some module")

    # The interpreter satisfies the pin, so a SyntaxError is a genuine code bug
    # and must not be masked as a stale-venv notice (no silent failures).
    with pytest.raises(SyntaxError, match="a real bug"):
        launcher.main(import_cli_main=_boom, version_info=(3, 14, 0), stderr=written.append)

    assert written == []  # no remediation emitted for a real bug
