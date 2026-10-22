"""The Python interpreter source and floor are pinned and consistent (#1063, #1681).

The 3.12 -> 3.14 migration regressed because the runtime side was never pinned:
with no `[tool.uv]` preference, `uv sync` seeded the venv from whatever
satisfied `requires-python` — the base OS 3.12. `python-preference =
"only-managed"` closes that: the venv is always seeded from a uv-managed
interpreter, never the host's.

The Python VERSION has one source, the `requires-python` floor (#1681,
vergil-project/.github#407): no `.python-version`, and `requires-python` carries
only a floor. CI keeps a `[ci].versions` pin equal to that floor until the lab
can follow the window (#1685). These tests lock that shape in place and keep the
floor agreeing with `launcher.MIN_PYTHON`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from mqlab import launcher

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
VERGIL_TOML = REPO_ROOT / "vergil.toml"


def _pyproject():
    return tomllib.loads(PYPROJECT.read_text())


def _floor(requires_python: str) -> tuple[int, ...]:
    assert requires_python.startswith(">="), f"requires-python is not a floor: {requires_python!r}"
    return tuple(int(part) for part in requires_python.removeprefix(">=").split("."))


def test_uv_pins_a_managed_interpreter() -> None:
    # only-managed keeps the venv off the base OS Python — the #1063 root cause.
    assert _pyproject()["tool"]["uv"]["python-preference"] == "only-managed"


def test_uv_never_auto_downloads_an_interpreter() -> None:
    # Without a .python-version, an automatic download would fetch the newest
    # CPython rather than the floor; scripts/setup installs the floor explicitly.
    assert _pyproject()["tool"]["uv"]["python-downloads"] == "manual"


def test_no_python_version_file() -> None:
    # A .python-version would pin the version behind the window's back (#1681).
    assert not (REPO_ROOT / ".python-version").exists(), ".python-version pins the version"


def test_ci_pin_is_the_floor_the_lab_runs() -> None:
    # Kept pin (#1685): CI must test the interpreter scripts/setup installs on
    # the VM — the requires-python floor — until the pin is removed.
    ci = tomllib.loads(VERGIL_TOML.read_text()).get("ci", {})
    floor = _pyproject()["project"]["requires-python"].removeprefix(">=")
    assert ci.get("versions") == [floor], "[ci].versions must pin exactly the floor"


def test_requires_python_is_a_floor_only() -> None:
    # A floor with no upper bound: the window, not this file, caps the version.
    requires_python = _pyproject()["project"]["requires-python"]
    assert "," not in requires_python, f"requires-python has an upper bound: {requires_python!r}"
    assert "<" not in requires_python, f"requires-python has an upper bound: {requires_python!r}"


def test_requires_python_floor_matches_launcher() -> None:
    assert _floor(_pyproject()["project"]["requires-python"]) == launcher.MIN_PYTHON


def test_ruff_and_mypy_target_the_floor() -> None:
    pp = _pyproject()
    major, minor = launcher.MIN_PYTHON
    assert pp["tool"]["ruff"]["target-version"] == f"py{major}{minor}"
    assert pp["tool"]["mypy"]["python_version"] == f"{major}.{minor}"
