"""Behavioral tests for scripts/push-rhel-iso.sh (host-side RHEL ISO push).

The script is run with a stubbed ``gcloud`` on PATH and ``MQLAB_RHEL_ISO`` pointed
at a temp file, so no real cloud call happens. Covers the two #329 regressions:

1. A no-match instance lookup must fail LOUDLY (not silently abort on the
   ``read < <(...)`` under ``set -e``).
2. Instance resolution must filter by Vergil LABELS, not a ``name~`` substring
   (cloud names are now opaque ``vrg-<hash>``).

Multi-ISO and catalog mode (#1395) run a copy of the script inside a throwaway git
repo (tests.rhel_iso_tree), so sources resolve from that repo's build/state/.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.rhel_iso_tree import TWO_MAJORS, add_iso, catalog_isos, make_tree

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
if [[ -n "${GCLOUD_FAIL_SCP:-}" && "$args" == *"compute scp"*"${GCLOUD_FAIL_SCP}"* ]]; then
  exit 1
fi
exit 0
"""


def _run(
    tmp_path: Path,
    *,
    instances_out: str,
    remote_size: str,
    args: tuple[str, ...] = ("--iso", "dvd.iso"),
) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gcloud"
    stub.write_text(_GCLOUD_STUB)
    stub.chmod(0o755)

    iso = tmp_path / "downloaded.iso"
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
        ["bash", str(SCRIPT), *args],
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


def test_iso_flag_is_required(tmp_path: Path) -> None:
    # #1274: the ISO filename comes from the catalog (os.rhel.<major>.iso); the script
    # names no RHEL version itself, so --iso (or --catalog, #1395) is required.
    proc, log = _run(tmp_path, instances_out="vrg-test us-central1-f", remote_size="0", args=())
    assert proc.returncode == 2
    assert "--iso or --catalog is required" in proc.stderr
    assert log == ""  # refused before any cloud call


def test_iso_flag_rejects_a_path(tmp_path: Path) -> None:
    proc, _log = _run(tmp_path, instances_out="", remote_size="0", args=("--iso", "/abs/dvd.iso"))
    assert proc.returncode == 2
    assert "filename, not a path" in proc.stderr


def test_unknown_arg_dies(tmp_path: Path) -> None:
    proc, _log = _run(tmp_path, instances_out="", remote_size="0", args=("--bogus",))
    assert proc.returncode == 2
    assert "unknown arg: --bogus" in proc.stderr


def test_copies_to_the_catalog_iso_name(tmp_path: Path) -> None:
    # The source may be any file (MQLAB_RHEL_ISO); it lands on the VM under the --iso name
    # so the VM's build finds it at build/state/<iso>.
    proc, log = _run(tmp_path, instances_out="vrg-test us-central1-f", remote_size="0")
    assert proc.returncode == 0, proc.stderr
    assert "build/state/dvd.iso" in log


# --- multi --iso and --catalog (#1395) ---------------------------------------------


def _run_tree(
    tmp_path: Path,
    root: Path,
    *args: str,
    remote_size: str = "0",
    fail_scp: str = "",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the tree's copy of the script from inside the tree, with the gcloud stub."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "gcloud"
    stub.write_text(_GCLOUD_STUB)
    stub.chmod(0o755)
    log = tmp_path / "gcloud.log"
    env = {k: v for k, v in os.environ.items() if k not in {"MQLAB_RHEL_ISO", "RHEL_ISO"}}
    env.update(
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "GCLOUD_LOG": str(log),
            "GCLOUD_INSTANCES_OUT": "vrg-test us-central1-f",
            "GCLOUD_REMOTE_SIZE": remote_size,
            "GCLOUD_FAIL_SCP": fail_scp,
            **(extra_env or {}),
        }
    )
    proc = subprocess.run(  # noqa: S603
        ["bash", str(root / "scripts" / "push-rhel-iso.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    return proc, (log.read_text().splitlines() if log.exists() else [])


def _scps(log: list[str]) -> list[str]:
    return [line for line in log if line.startswith("compute scp")]


def test_repeated_iso_pushes_each_with_one_instance_lookup(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    add_iso(root, "a-dvd.iso")
    add_iso(root, "b-dvd.iso")
    proc, log = _run_tree(tmp_path, root, "--iso", "a-dvd.iso", "--iso", "b-dvd.iso")
    assert proc.returncode == 0, proc.stderr
    # The VM is resolved once for every ISO, never per file.
    assert sum("instances list" in line for line in log) == 1
    scps = _scps(log)
    assert len(scps) == 2
    assert "build/state/a-dvd.iso" in scps[0]
    assert "build/state/b-dvd.iso" in scps[1]
    assert "all 2 RHEL DVD ISOs" in proc.stdout


def test_same_size_copies_are_skipped_per_iso(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    add_iso(root, "a-dvd.iso")
    add_iso(root, "b-dvd.iso")
    proc, log = _run_tree(
        tmp_path, root, "--iso", "a-dvd.iso", "--iso", "b-dvd.iso", remote_size="4096"
    )
    assert proc.returncode == 0, proc.stderr
    assert _scps(log) == []
    assert proc.stdout.count("already staged:") == 2


def test_failures_are_aggregated_and_named(tmp_path: Path) -> None:
    # One source is missing and one copy fails: the third ISO is still pushed, and the
    # run exits non-zero naming BOTH failures.
    root = make_tree(tmp_path)
    add_iso(root, "good-dvd.iso")
    add_iso(root, "flaky-dvd.iso")
    proc, log = _run_tree(
        tmp_path,
        root,
        *("--iso", "missing-dvd.iso", "--iso", "flaky-dvd.iso", "--iso", "good-dvd.iso"),
        fail_scp="flaky-dvd.iso",
    )
    assert proc.returncode == 1
    assert "RHEL DVD ISO not found at" in proc.stderr
    assert "copy of flaky-dvd.iso to the VM failed" in proc.stderr
    assert "failed to push 2 of 3 RHEL DVD ISO(s): missing-dvd.iso flaky-dvd.iso" in proc.stderr
    assert any("build/state/good-dvd.iso" in line for line in _scps(log))
    assert "done: good-dvd.iso" in proc.stdout


def test_all_sources_missing_fails_before_any_cloud_call(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    proc, log = _run_tree(tmp_path, root, "--iso", "a-dvd.iso", "--iso", "b-dvd.iso")
    assert proc.returncode == 1
    assert "failed to push 2 of 2 RHEL DVD ISO(s): a-dvd.iso b-dvd.iso" in proc.stderr
    assert log == []


def test_catalog_pushes_every_rhel_dvd_in_the_committed_catalog(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    expected = catalog_isos()
    for iso in expected:
        add_iso(root, iso)
    proc, log = _run_tree(tmp_path, root, "--catalog")
    assert proc.returncode == 0, proc.stderr
    scps = _scps(log)
    assert len(scps) == len(expected)
    for scp, iso in zip(scps, expected, strict=True):
        assert f"build/state/{iso}" in scp


def test_catalog_with_two_rhel_majors(tmp_path: Path) -> None:
    root = make_tree(tmp_path, TWO_MAJORS)
    add_iso(root, "alpha-dvd.iso")
    add_iso(root, "beta-dvd.iso")
    proc, log = _run_tree(tmp_path, root, "--catalog")
    assert proc.returncode == 0, proc.stderr
    scps = _scps(log)
    assert len(scps) == 2
    assert "build/state/alpha-dvd.iso" in scps[0]
    assert "build/state/beta-dvd.iso" in scps[1]
    assert not any("not-rhel.iso" in line for line in log)


def test_unreadable_catalog_fails_loudly(tmp_path: Path) -> None:
    root = make_tree(tmp_path, "os:\n  rhel:\n    9:\n      iso: a.iso\n")
    proc, log = _run_tree(tmp_path, root, "--catalog")
    assert proc.returncode == 1
    assert "one-line flow mapping" in proc.stderr
    assert "could not resolve the RHEL DVD list from the catalog" in proc.stderr
    assert log == []


@pytest.mark.parametrize("var", ["MQLAB_RHEL_ISO", "RHEL_ISO"])
@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("--catalog",), "ambiguous with --catalog"),
        (("--iso", "a.iso", "--iso", "b.iso"), "ambiguous with 2 --iso values"),
    ],
)
def test_override_is_refused_when_ambiguous(
    tmp_path: Path, var: str, args: tuple[str, ...], why: str
) -> None:
    root = make_tree(tmp_path)
    src = add_iso(root, "downloaded.iso")
    proc, log = _run_tree(tmp_path, root, *args, extra_env={var: str(src)})
    assert proc.returncode == 2
    assert "MQLAB_RHEL_ISO/RHEL_ISO names one source file" in proc.stderr
    assert why in proc.stderr
    assert log == []


def test_catalog_and_iso_are_mutually_exclusive(tmp_path: Path) -> None:
    root = make_tree(tmp_path)
    proc, log = _run_tree(tmp_path, root, "--catalog", "--iso", "a.iso")
    assert proc.returncode == 2
    assert "--catalog and --iso are mutually exclusive" in proc.stderr
    assert log == []


@pytest.mark.parametrize("value", ["build/state/a.iso", "./a.iso", "a/"])
def test_a_later_iso_value_must_be_a_filename(tmp_path: Path, value: str) -> None:
    proc, log = _run(
        tmp_path, instances_out="", remote_size="0", args=("--iso", "a.iso", "--iso", value)
    )
    assert proc.returncode == 2
    assert f"filename, not a path (got '{value}')" in proc.stderr
    assert log == ""


def test_iso_without_a_value_dies(tmp_path: Path) -> None:
    proc, log = _run(tmp_path, instances_out="", remote_size="0", args=("--iso",))
    assert proc.returncode == 2
    assert "--iso needs a filename" in proc.stderr
    assert log == ""
