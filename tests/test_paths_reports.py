from __future__ import annotations

from mqlab.paths import repo_root, reports_dir


def test_reports_dir_is_under_build() -> None:
    assert reports_dir() == repo_root() / "build" / "reports"
