"""No pymqi reference outside historical records (epic .github#300 definition of done).

The lab's MQI binding is ibmmq. Every git-tracked file is scanned, case-insensitively,
for "pymqi". The allowlist is FIXED: dated, historical records only.

- docs/plans/**, docs/specs/**: design history;
- docs/reports/**: dated reports (e.g. the guest-runtime spike proved pymqi);
- CHANGELOG.md, releases/**: generated from commit history;
- this file.

A hit is fixed at its source (reword it). Never widen the allowlist to silence one.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

NEEDLE = re.compile(r"pymqi", re.IGNORECASE)
HISTORICAL_PREFIXES = ("docs/plans/", "docs/specs/", "docs/reports/", "releases/")
HISTORICAL_FILES = frozenset({"CHANGELOG.md", "tests/test_no_pymqi_refs.py"})


def _is_historical(rel: str) -> bool:
    return rel in HISTORICAL_FILES or rel.startswith(HISTORICAL_PREFIXES)


def _tracked(repo: Path) -> list[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(repo), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git ls-files failed in {repo} (exit {result.returncode}): {result.stderr.strip()}"
        raise RuntimeError(msg)
    return sorted(filter(None, result.stdout.split("\0")))


def hits(repo: Path) -> list[str]:
    found = []
    for rel in _tracked(repo):
        path = repo / rel
        if _is_historical(rel) or not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary asset (images, archives): not prose or code
        found.extend(
            f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1) if NEEDLE.search(line)
        )
    return found


def test_allowlist_is_history_only():
    assert _is_historical("docs/specs/x.md")
    assert _is_historical("docs/reports/assets/guest-runtime-spike/spike.yml")
    assert _is_historical("CHANGELOG.md")
    assert not _is_historical("docs/site/docs/operate/guest-components.md")
    assert not _is_historical("docs/development/box-model.md")
    assert not _is_historical("components/mq-resiliency-clients/src/mqrc/selfcheck.py")


def test_scan_finds_a_planted_reference(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603,S607
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "a.py").write_text("import os\nimport PyMQI\n")
    (tmp_path / "docs" / "specs" / "old.md").write_text("pymqi was used\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)  # noqa: S603,S607
    assert hits(tmp_path) == ["a.py:2"]


def test_scan_reports_a_git_failure(tmp_path):
    with pytest.raises(RuntimeError, match="git ls-files failed"):
        hits(tmp_path)  # not a git repo


def test_no_pymqi_reference_outside_history():
    found = hits(repo_root())
    assert not found, "pymqi references outside history (reword at source):\n" + "\n".join(found)
