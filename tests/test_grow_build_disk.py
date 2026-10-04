"""Behavioral tests for lab/boxes/_grow-build-disk.sh (#1340).

build-fatbox.sh grows the transient build disk to 18G so the heaviest Ubuntu bakes stop
overflowing the ~8.7G cloud image (#1144/#1147). It used to resize UNCONDITIONALLY to an
absolute 18G, which is a shrink for the 20G RHEL base (rhel/build-box.sh creates it at
20G). qemu-img refuses a shrink, so every RHEL fat-box bake died with exit 1. The helper
is grow-only: it resizes when the image is smaller than the target and otherwise leaves
it alone. It never shrinks, because shrinking a partitioned image truncates data.

Tested with stubbed ``sudo`` (pass-through) and ``qemu-img`` on PATH, so no libvirt or
root is needed. Modelled on tests/test_await_install.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "lab" / "boxes" / "_grow-build-disk.sh"
FATBOX = ROOT / "lab" / "boxes" / "build-fatbox.sh"

GIB = 1024**3

_SUDO_STUB = """#!/usr/bin/env bash
exec "$@"
"""

# `info --output=json <img>` prints a qemu-img-shaped document (including a nested
# child node with its own virtual-size, as real qcow2 output has). `resize` is recorded
# to $QEMU_LOG. QEMU_INFO_FAIL=1 makes `info` fail.
_QEMU_IMG_STUB = """#!/usr/bin/env bash
case "$1" in
  info)
    [ "${QEMU_INFO_FAIL:-0}" = 1 ] && { echo "qemu-img: Could not open image" >&2; exit 1; }
    cat <<EOF
{
    "children": [{"name": "file", "info": {"virtual-size": 1197377536}}],
    "virtual-size": ${QEMU_VSIZE},
    "filename": "${@: -1}",
    "format": "qcow2"
}
EOF
    ;;
  resize) echo "resize ${*:2}" >> "$QEMU_LOG" ;;
  *) echo "unexpected qemu-img call: $*" >&2; exit 99 ;;
esac
"""


def _run(tmp_path: Path, *, vsize: int, info_fail: bool = False) -> tuple[int, str, list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("sudo", _SUDO_STUB), ("qemu-img", _QEMU_IMG_STUB)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)
    log = tmp_path / "qemu.log"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "QEMU_VSIZE": str(vsize),
        "QEMU_LOG": str(log),
        "QEMU_INFO_FAIL": "1" if info_fail else "0",
    }
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(SCRIPT), "/pool/fatbox-x-build.qcow2", "18"],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


def test_grows_a_smaller_ubuntu_base_to_the_target(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, vsize=9_336_520_704)  # ~8.7 GiB cloud image
    assert rc == 0, out
    assert calls == ["resize /pool/fatbox-x-build.qcow2 18G"]


def test_never_shrinks_the_20g_rhel_base(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, vsize=20 * GIB)
    assert rc == 0, out
    assert calls == [], "a 20G RHEL base must not be resized down to 18G (#1340)"


def test_leaves_an_image_already_at_the_target(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, vsize=18 * GIB)
    assert rc == 0, out
    assert calls == []


def test_fails_loudly_when_the_size_cannot_be_read(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, vsize=0, info_fail=True)
    assert rc != 0
    assert calls == [], "never resize blind"
    assert "Could not open image" in out


def test_fatbox_builder_resizes_only_through_the_grow_only_helper() -> None:
    src = FATBOX.read_text()
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("qemu-img resize" in ln for ln in code), (
        "build-fatbox.sh must not call `qemu-img resize` directly; use _grow-build-disk.sh "
        "so a larger base (RHEL, 20G) is never shrunk (#1340)"
    )
    assert any("_grow-build-disk.sh" in ln for ln in code)
