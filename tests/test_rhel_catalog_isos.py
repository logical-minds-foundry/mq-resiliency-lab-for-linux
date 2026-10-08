"""lab/scripts/rhel-catalog-isos.sh: the dependency-free catalog reader (#1395).

It backs ``--catalog`` in push-rhel-iso.sh (macOS host: bash 3.2 + BSD awk, no
mqlab/PyYAML) and stage-rhel-iso.sh. It is a deliberately narrow reader, so these tests
pin it two ways: against mqlab.versions on the COMMITTED catalog (a catalog reformat
that the reader cannot follow fails here, not silently in the field), and against every
shape it must refuse loudly.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.rhel_iso_tree import REPO, TWO_MAJORS, catalog_isos

if TYPE_CHECKING:
    from pathlib import Path

SCRIPT = REPO / "lab" / "scripts" / "rhel-catalog-isos.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ),
    )


def _catalog(tmp_path: Path, text: str) -> str:
    path = tmp_path / "versions.yaml"
    path.write_text(text)
    return str(path)


def test_committed_catalog_matches_mqlab_versions():
    # The default catalog is lab/versions.yaml, read relative to the script.
    result = _run()
    assert result.returncode == 0, result.stderr
    expected = catalog_isos()
    assert expected  # the catalog names at least one RHEL DVD
    assert result.stdout.splitlines() == expected


def test_two_majors_in_catalog_order(tmp_path: Path):
    result = _run(_catalog(tmp_path, TWO_MAJORS))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["alpha-dvd.iso", "beta-dvd.iso"]


def test_too_many_args_is_usage_error():
    result = _run("a.yaml", "b.yaml")
    assert result.returncode == 2
    assert "usage: rhel-catalog-isos.sh" in result.stderr


def test_missing_catalog_fails_loudly(tmp_path: Path):
    absent = tmp_path / "absent.yaml"
    result = _run(str(absent))
    assert result.returncode == 1
    assert f"OS catalog not found: {absent}" in result.stderr


_BAD = {
    "no_os": ("roles: {}\n", "no top-level os: block"),
    "no_rhel": ("os:\n  ubuntu:\n    24: { base_box: x }\n", "no os.rhel block"),
    "empty_rhel": ("os:\n  rhel:\nroles: {}\n", "os.rhel names no DVD ISO"),
    "flow_os": ("os: { rhel: { 9: { iso: a.iso } } }\n", "os: is not a block mapping"),
    "dup_os": ("os:\n  rhel:\n    9: { iso: a.iso }\nos:\n", "duplicate top-level os:"),
    "flow_rhel": ("os:\n  rhel: { 9: { iso: a.iso } }\n", "os.rhel is not a block mapping"),
    "dup_rhel": (
        "os:\n  rhel:\n    9: { iso: a.iso }\n  ubuntu:\n    24: { b: x }\n  rhel:\n",
        "duplicate os.rhel",
    ),
    "block_entry": ("os:\n  rhel:\n    9:\n      iso: a.iso\n", "one-line flow mapping"),
    "multiline_flow": ("os:\n  rhel:\n    9: { base_box: x,\n      iso: a.iso }\n", "one-line"),
    "no_iso": ("os:\n  rhel:\n    9: { base_box: x }\n", "has no iso: key"),
    "two_isos": ("os:\n  rhel:\n    9: { iso: a.iso, iso: b.iso }\n", "more than one iso:"),
    "unterminated": ('os:\n  rhel:\n    9: { iso: "a.iso }\n', "unterminated quoted"),
    "not_iso": ("os:\n  rhel:\n    9: { iso: a.img }\n", "not a plain *.iso filename"),
    "path_iso": ("os:\n  rhel:\n    9: { iso: dir/a.iso }\n", "not a plain *.iso filename"),
    "empty_iso": ("os:\n  rhel:\n    9: { iso: , base_box: x }\n", "not a plain *.iso filename"),
}


@pytest.mark.parametrize("case", sorted(_BAD))
def test_unexpected_shapes_fail_loudly(tmp_path: Path, case: str):
    text, message = _BAD[case]
    result = _run(_catalog(tmp_path, text))
    assert result.returncode == 1, result.stdout
    assert "ERROR:" in result.stderr
    assert message in result.stderr
    assert result.stdout == ""  # never a partial list on failure


def test_single_quoted_iso_is_unquoted(tmp_path: Path):
    result = _run(_catalog(tmp_path, "os:\n  rhel:\n    9: { iso: 'q-dvd.iso' }\n"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["q-dvd.iso"]
