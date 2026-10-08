"""lab/scripts/stage-rhel-iso.sh takes DVD filenames as --iso, or --catalog (#1274, #1395).

The filename is the catalog's os.rhel.<major>.iso, which mqlab passes; the script names
no RHEL version itself. The arg validation runs the real script. Staging runs a copy
inside a throwaway git repo (tests.rhel_iso_tree) whose stage-iso-into-pool.sh is a
logging stub, so no test touches /var/lib/libvirt/images.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.rhel_iso_tree import TWO_MAJORS, add_iso, catalog_isos, make_tree

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "scripts" / "stage-rhel-iso.sh"
POOL = "/var/lib/libvirt/images"


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
    assert "--iso or --catalog is required" in result.stderr


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


# --- staging through a stubbed pool helper (#1395) ---------------------------------


def _run_tree(
    tmp_path: Path, root: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = tmp_path / "pool.log"
    base = {k: v for k, v in os.environ.items() if k not in {"MQLAB_RHEL_ISO", "RHEL_ISO"}}
    result = subprocess.run(  # noqa: S603
        ["bash", str(root / "lab" / "scripts" / "stage-rhel-iso.sh"), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env={**base, "POOL_LOG": str(log), **(env or {})},
    )
    return result, (log.read_text().splitlines() if log.exists() else [])


def test_single_iso_stages_from_build_state(tmp_path: Path):
    root = make_tree(tmp_path)
    src = add_iso(root, "a-dvd.iso")
    result, log = _run_tree(tmp_path, root, "--iso", "a-dvd.iso")
    assert result.returncode == 0, result.stderr
    [(staged_src, dest)] = [line.split() for line in log]
    assert Path(staged_src).resolve() == src.resolve()
    assert dest == f"{POOL}/a-dvd.iso"


def test_single_iso_honours_the_override(tmp_path: Path):
    root = make_tree(tmp_path)
    src = tmp_path / "downloaded.iso"
    src.write_bytes(b"x")
    result, log = _run_tree(tmp_path, root, "--iso", "a-dvd.iso", env={"RHEL_ISO": str(src)})
    assert result.returncode == 0, result.stderr
    assert log == [f"{src} {POOL}/a-dvd.iso"]


def test_repeated_iso_stages_each(tmp_path: Path):
    root = make_tree(tmp_path)
    add_iso(root, "a-dvd.iso")
    add_iso(root, "b-dvd.iso")
    result, log = _run_tree(tmp_path, root, "--iso", "a-dvd.iso", "--iso", "b-dvd.iso")
    assert result.returncode == 0, result.stderr
    assert [line.split()[1] for line in log] == [f"{POOL}/a-dvd.iso", f"{POOL}/b-dvd.iso"]
    assert "staged all 2 RHEL DVD ISOs." in result.stdout


def test_failures_are_aggregated_and_named(tmp_path: Path):
    # A missing source and a failing pool stage: the remaining ISO is still staged and
    # the run exits non-zero naming both failures.
    root = make_tree(tmp_path)
    add_iso(root, "bad-dvd.iso")
    add_iso(root, "good-dvd.iso")
    result, log = _run_tree(
        tmp_path,
        root,
        *("--iso", "missing-dvd.iso", "--iso", "bad-dvd.iso", "--iso", "good-dvd.iso"),
        env={"POOL_FAIL_ON": "bad-dvd.iso"},
    )
    assert result.returncode == 1
    assert "RHEL DVD ISO not found at" in result.stderr
    assert "failed to stage 2 of 3 RHEL DVD ISO(s): missing-dvd.iso bad-dvd.iso" in result.stderr
    assert [line.split()[1] for line in log] == [f"{POOL}/bad-dvd.iso", f"{POOL}/good-dvd.iso"]


def test_catalog_stages_every_rhel_dvd_in_the_committed_catalog(tmp_path: Path):
    root = make_tree(tmp_path)
    expected = catalog_isos()
    for iso in expected:
        add_iso(root, iso)
    result, log = _run_tree(tmp_path, root, "--catalog")
    assert result.returncode == 0, result.stderr
    assert [line.split()[1] for line in log] == [f"{POOL}/{iso}" for iso in expected]


def test_catalog_with_two_rhel_majors(tmp_path: Path):
    root = make_tree(tmp_path, TWO_MAJORS)
    add_iso(root, "alpha-dvd.iso")
    add_iso(root, "beta-dvd.iso")
    result, log = _run_tree(tmp_path, root, "--catalog")
    assert result.returncode == 0, result.stderr
    assert [line.split()[1] for line in log] == [f"{POOL}/alpha-dvd.iso", f"{POOL}/beta-dvd.iso"]


def test_unreadable_catalog_fails_loudly(tmp_path: Path):
    root = make_tree(tmp_path, "os:\n  ubuntu:\n    24: { base_box: x }\n")
    result, log = _run_tree(tmp_path, root, "--catalog")
    assert result.returncode == 1
    assert "no os.rhel block" in result.stderr
    assert "could not resolve the RHEL DVD list from the catalog" in result.stderr
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
):
    root = make_tree(tmp_path)
    src = add_iso(root, "downloaded.iso")
    result, log = _run_tree(tmp_path, root, *args, env={var: str(src)})
    assert result.returncode == 2
    assert "MQLAB_RHEL_ISO/RHEL_ISO names one source file" in result.stderr
    assert why in result.stderr
    assert log == []


def test_catalog_and_iso_are_mutually_exclusive():
    result = _run("--iso", "a.iso", "--catalog")
    assert result.returncode == 2
    assert "--catalog and --iso are mutually exclusive" in result.stderr


@pytest.mark.parametrize("value", ["build/state/a.iso", "./a.iso"])
def test_a_later_iso_value_must_be_a_filename(value: str):
    result = _run("--iso", "a.iso", "--iso", value)
    assert result.returncode == 2
    assert f"not a path (got '{value}')" in result.stderr


def test_iso_without_a_value_dies():
    result = _run("--iso")
    assert result.returncode == 2
    assert "--iso needs a filename" in result.stderr
