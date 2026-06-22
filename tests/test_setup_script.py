from __future__ import annotations

import stat
import subprocess
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "scripts" / "setup"


def test_setup_script_exists_and_is_executable() -> None:
    assert SETUP.is_file(), "scripts/setup is missing"
    assert SETUP.stat().st_mode & stat.S_IXUSR, "scripts/setup is not executable"


def test_setup_script_is_valid_bash() -> None:
    # `bash -n` parses without executing — catches syntax errors.
    result = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_setup_script_is_strict_and_points_at_next_command() -> None:
    text = SETUP.read_text()
    assert "set -euo pipefail" in text, "script must fail loud"
    assert "uv sync" in text, "script must materialize the environment"
    assert "mqlab bootstrap" in text, "script must point the user at the bring-up command"
    assert "mqlab doctor" in text, "script must point the user at the pre-flight gate"
