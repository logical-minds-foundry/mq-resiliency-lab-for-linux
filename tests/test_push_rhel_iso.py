"""Behavioral tests for scripts/push-rhel-iso.sh (host-side RHEL ISO push).

The script is run with a stubbed ``gcloud`` on PATH and ``MQLAB_RHEL_ISO`` pointed
at a temp file, so no real cloud call happens. Covers the two #329 regressions:

1. A no-match instance lookup must fail LOUDLY (not silently abort on the
   ``read < <(...)`` under ``set -e``).
2. Instance resolution must filter by Vergil LABELS, not a ``name~`` substring
   (cloud names are now opaque ``vrg-<hash>``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "push-rhel-iso.sh"

# A gcloud stub: logs its argv, returns a configurable instances-list result, and
# answers the remote size probe from an env var so the script can reach its
# idempotent "already staged" exit without a real scp.
_GCLOUD_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GCLOUD_LOG"
args="$*"
if [[ "$args" == *"instances list"* ]]; then
  printf '%s' "${GCLOUD_INSTANCES_OUT:-}"
  exit 0
fi
if [[ "$args" == *"stat -c%s"* ]]; then
  printf '%s' "${GCLOUD_REMOTE_SIZE:-0}"
  exit 0
fi
exit 0
"""


def _run(
    tmp_path: Path, *, instances_out: str, remote_size: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gcloud"
    stub.write_text(_GCLOUD_STUB)
    stub.chmod(0o755)

    iso = tmp_path / "rhel-9.6-x86_64-dvd.iso"
    iso.write_bytes(b"x" * 4096)  # local_size = 4096
    log = tmp_path / "gcloud.log"

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "MQLAB_RHEL_ISO": str(iso),
        "GCLOUD_LOG": str(log),
        "GCLOUD_INSTANCES_OUT": instances_out,
        "GCLOUD_REMOTE_SIZE": remote_size,
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    return proc, (log.read_text() if log.exists() else "")


def test_no_matching_instance_fails_loudly(tmp_path: Path) -> None:
    # gcloud finds no instance -> the script must exit non-zero AND say why,
    # rather than silently aborting on the read (#329).
    proc, _log = _run(tmp_path, instances_out="", remote_size="0")
    assert proc.returncode != 0
    assert "no off-platform vm" in proc.stderr.lower()


def test_resolves_instance_by_label_not_name(tmp_path: Path) -> None:
    # A same-size remote copy makes the script no-op at the idempotent check; we
    # only assert HOW it looked the instance up: by Vergil labels, not name~ (#329).
    proc, log = _run(tmp_path, instances_out="vrg-test us-central1-f", remote_size="4096")
    assert proc.returncode == 0, proc.stderr
    assert "labels.vergil-repo=mq-resiliency-lab-for-linux" in log
    assert "labels.vergil-org=logical-minds-foundry" in log
    assert "name~mq-resiliency-lab" not in log
