"""build-fatbox.sh must require --arch and die loudly when it is missing/invalid.

mqlab always supplies `--arch <aarch64|x86_64>` (the orchestrator computes it from
host facts via platforms.box_build_arch and passes it next to --domain-type/--cpu-mode,
#103 D2) together with every catalog input (#1274); a hand-run is told what to pass.
This exercises only the early arg-validation path, which runs before any
git/virsh/cache side effect, so it needs no libvirt. Deterministic across host arch —
asserts a non-zero exit, not an arch-specific message."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.boxfleet import fatbox_args

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "build-fatbox.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _args_with_arch(value: str | None) -> list[str]:
    args = fatbox_args("mq-client-ubuntu24")
    i = args.index("--arch")
    if value is None:
        return args[:i] + args[i + 2 :]
    return [*args[: i + 1], value, *args[i + 2 :]]


def test_missing_arch_dies():
    result = _run(*_args_with_arch(None))
    assert result.returncode != 0
    assert "--arch must be 'aarch64' or 'x86_64'" in result.stderr


def test_invalid_arch_dies():
    result = _run(*_args_with_arch("bogus"))
    assert result.returncode != 0
    assert "(got 'bogus')" in result.stderr
