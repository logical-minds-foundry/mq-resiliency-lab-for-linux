"""build-box.sh must require --domain-type/--cpu-mode and die loudly when they are
missing — the orchestrator always supplies them; a hand-run is told what to pass
(#327, design D3/D5). This exercises only the early arg-validation path, which
runs before any git/virsh/cache side effect, so it needs no libvirt."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "rhel96" / "build-box.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_args_dies_with_usage():
    result = _run()
    assert result.returncode != 0
    assert "--domain-type" in (result.stderr + result.stdout)


def test_missing_cpu_mode_dies_with_usage():
    result = _run("--domain-type", "kvm")
    assert result.returncode != 0
    assert "--cpu-mode" in (result.stderr + result.stdout)


def test_invalid_domain_type_dies():
    result = _run("--domain-type", "bogus", "--cpu-mode", "maximum")
    assert result.returncode != 0
