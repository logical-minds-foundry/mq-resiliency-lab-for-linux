"""The Python interpreter pin is present and internally consistent (#1063).

The 3.12 -> 3.14 migration regressed because the runtime side was never pinned:
with no `.python-version` and no `[tool.uv]` preference, `uv sync` seeded the
venv from whatever satisfied `requires-python` — the base OS 3.12. These tests
lock the pin in place and assert every source that names the version agrees, so
the pin can never silently drift from `requires-python` again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from mqlab import launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"


def _pyproject():
    return tomllib.loads(PYPROJECT.read_text())


def test_python_version_file_pins_314() -> None:
    assert PYTHON_VERSION_FILE.is_file(), ".python-version is missing — interpreter not pinned"
    assert PYTHON_VERSION_FILE.read_text().strip() == "3.14"


def test_requires_python_is_314() -> None:
    assert _pyproject()["project"]["requires-python"] == ">=3.14,<3.15"


def test_uv_pins_a_managed_interpreter() -> None:
    # only-managed keeps the venv off the base OS Python — the #1063 root cause.
    assert _pyproject()["tool"]["uv"]["python-preference"] == "only-managed"


def test_ruff_and_mypy_target_314() -> None:
    pp = _pyproject()
    assert pp["tool"]["ruff"]["target-version"] == "py314"
    assert pp["tool"]["mypy"]["python_version"] == "3.14"


def test_pin_is_consistent_across_sources() -> None:
    # .python-version, the lower bound of requires-python, and launcher.MIN_PYTHON
    # must all name the same feature version — one pin, cross-checked here.
    pinned = tuple(int(part) for part in PYTHON_VERSION_FILE.read_text().strip().split("."))
    lower_bound = _pyproject()["project"]["requires-python"].split(",")[0].removeprefix(">=")
    lower_tuple = tuple(int(part) for part in lower_bound.split("."))

    assert pinned == launcher.MIN_PYTHON
    assert lower_tuple == launcher.MIN_PYTHON
