"""lab/scripts/stage-rhel-iso.sh takes the DVD filename as --iso (#1274).

The filename is the catalog's os.rhel.<major>.iso, which mqlab passes; the script names
no RHEL version itself. Only the arg validation and the missing-source failure are
exercised: the happy path stages into /var/lib/libvirt/images.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "scripts" / "stage-rhel-iso.sh"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_iso_is_required():
    result = _run()
    assert result.returncode == 2
    assert "--iso is required" in result.stderr


def test_iso_must_be_a_filename():
    result = _run("--iso", "/abs/dvd.iso")
    assert result.returncode == 2
    assert "not a path" in result.stderr


def test_unknown_arg_dies():
    result = _run("--bogus")
    assert result.returncode == 2
    assert "unknown arg: --bogus" in result.stderr


def test_missing_source_names_the_iso(tmp_path: Path):
    absent = tmp_path / "absent.iso"
    result = _run("--iso", "dvd-x.iso", env={"MQLAB_RHEL_ISO": str(absent)})
    assert result.returncode == 1
    assert f"RHEL DVD ISO not found at {absent}" in result.stderr
    assert "MQLAB_RHEL_ISO=/path/to/dvd-x.iso" in result.stderr
