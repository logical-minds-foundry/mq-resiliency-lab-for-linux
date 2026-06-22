from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_GUARD_PATH = REPO_ROOT / "tools" / "release_version_guard.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_version_guard", _GUARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_tag_version_strips_leading_v() -> None:
    g = _load()
    assert g.parse_tag_version("v1.2.0") == "1.2.0"


def test_parse_tag_version_passthrough_without_v() -> None:
    g = _load()
    assert g.parse_tag_version("1.2.0") == "1.2.0"


def test_read_pyproject_version(tmp_path) -> None:
    g = _load()
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "x"\nversion = "3.4.5"\n')
    assert g.read_pyproject_version(p) == "3.4.5"


def test_read_version_file_strips_whitespace(tmp_path) -> None:
    g = _load()
    p = tmp_path / "VERSION"
    p.write_text("3.4.5\n")
    assert g.read_version_file(p) == "3.4.5"


def test_assert_versions_match_passes_when_equal() -> None:
    g = _load()
    g.assert_versions_match("1.2.0", "1.2.0", "1.2.0")  # no raise


def test_assert_versions_match_raises_on_mismatch() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.assert_versions_match("1.2.0", "1.2.1", "1.2.0")


def test_main_ok_against_repo_files() -> None:
    # The repo's pyproject.toml and VERSION agree; tag must match them.
    g = _load()
    version = g.read_version_file(REPO_ROOT / "VERSION")
    g.main(["prog", f"v{version}"])  # no raise


def test_main_usage_error_without_tag() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.main(["prog"])
