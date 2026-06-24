"""Behavioral tests for lab/scripts/stage-iso-into-pool.sh (#337).

On a local filesystem (the cloud /vergil case) the helper must SYMLINK the pool
destination to the source rather than copy it — so the big ISO never lands on the
small boot disk that backs the pool. Run as a subprocess; the test's tmp dir is a
local fs, so it exercises the symlink branch. (The host-passthrough copy branch
needs a 9p/virtiofs mount to exercise and is the pre-existing behaviour.)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "scripts" / "stage-iso-into-pool.sh"


def _run(src: Path, dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(src), str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_symlinks_local_source_into_pool(tmp_path: Path) -> None:
    src = tmp_path / "rhel-9.6-x86_64-dvd.iso"
    src.write_bytes(b"x" * 4096)
    pool = tmp_path / "pool"
    pool.mkdir()
    dest = pool / "rhel-9.6-x86_64-dvd.iso"

    proc = _run(src, dest)

    assert proc.returncode == 0, proc.stderr
    assert dest.is_symlink()  # a link, not a 12 GB copy on the boot disk
    assert dest.resolve() == src.resolve()


def test_relinking_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "rhel.iso"
    src.write_bytes(b"x" * 4096)
    dest = tmp_path / "pool-rhel.iso"

    assert _run(src, dest).returncode == 0
    proc = _run(src, dest)  # second run is a no-op

    assert proc.returncode == 0
    assert "already linked" in proc.stdout
    assert dest.is_symlink()
