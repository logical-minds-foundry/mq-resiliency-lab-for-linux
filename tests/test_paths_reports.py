from __future__ import annotations

import pytest

from mqlab.paths import repo_root, reports_dir


@pytest.mark.real_build_root
def test_reports_dir_is_under_state_bucket() -> None:
    assert reports_dir() == repo_root() / "build" / "state" / "reports"
