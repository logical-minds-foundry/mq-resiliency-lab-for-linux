"""build-fatbox.sh must require --arch and die loudly when it is missing/invalid.

mqlab always supplies `--arch <aarch64|x86_64>` (the orchestrator computes it from
host facts via platforms.box_build_arch and passes it next to --domain-type/--cpu-mode,
#103 D2); a hand-run is told what to pass. This exercises only the early arg-validation
path, which runs before any git/virsh/cache side effect, so it needs no libvirt.
Deterministic across host arch — asserts a non-zero exit, not an arch-specific message."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "build-fatbox.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_arch_dies():
    result = _run(
        "--box", "mq-ubuntu2404", "--domain-type", "kvm", "--cpu-mode", "host-passthrough"
    )
    assert result.returncode != 0
    assert "--arch" in (result.stderr + result.stdout)


def test_invalid_arch_dies():
    result = _run(
        "--box",
        "mq-ubuntu2404",
        "--arch",
        "bogus",
        "--domain-type",
        "kvm",
        "--cpu-mode",
        "host-passthrough",
    )
    assert result.returncode != 0
    assert "--arch" in (result.stderr + result.stdout)
