"""Behavioral tests for lab/boxes/rhel96/await-install.sh.

The helper waits for the transient RHEL build domain to power off at the end of
its kickstart install, emitting an elapsed + latest-console-line heartbeat each
poll. Tested with a stubbed ``virsh`` on PATH (reports ``running`` once, then
``shut off``) and a temp console log — no libvirt needed. (#331)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "rhel96" / "await-install.sh"

# domstate stub: "running" on the first call, "shut off" after — so the loop runs
# exactly one heartbeat then exits. Call count persists in $VIRSH_COUNTER.
_VIRSH_STUB = """#!/usr/bin/env bash
n=0
[ -f "$VIRSH_COUNTER" ] && n="$(cat "$VIRSH_COUNTER")"
n=$((n + 1))
printf '%s' "$n" > "$VIRSH_COUNTER"
if [ "$n" -le 1 ]; then echo "running"; else echo "shut off"; fi
"""


def _run(tmp_path: Path, *, console_text: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    virsh = bindir / "virsh"
    virsh.write_text(_VIRSH_STUB)
    virsh.chmod(0o755)

    log = tmp_path / "console.log"
    log.write_text(console_text)

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "VIRSH_COUNTER": str(tmp_path / "n"),
    }
    # poll_secs=0 so the test does not actually wait between heartbeats.
    return subprocess.run(
        ["bash", str(SCRIPT), "rhel96-build", str(log), "0"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_heartbeat_shows_elapsed_and_latest_console_line(tmp_path: Path) -> None:
    proc = _run(
        tmp_path,
        console_text="Setting up the installation environment\nInstalling: 542/1183 packages\n",
    )
    assert proc.returncode == 0, proc.stderr
    assert "[install]" in proc.stdout
    assert "Installing: 542/1183 packages" in proc.stdout
    assert "done in" in proc.stdout


def test_heartbeat_degrades_when_console_empty(tmp_path: Path) -> None:
    proc = _run(tmp_path, console_text="")
    assert proc.returncode == 0, proc.stderr
    assert "(no console output yet)" in proc.stdout
