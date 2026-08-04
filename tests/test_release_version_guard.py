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


def test_base_version_strips_prerelease_suffix() -> None:
    g = _load()
    assert g.base_version("1.0.0-rc1") == "1.0.0"


def test_base_version_passthrough_for_release() -> None:
    g = _load()
    assert g.base_version("1.0.0") == "1.0.0"


def test_is_prerelease_true_for_rc() -> None:
    g = _load()
    assert g.is_prerelease("1.0.0-rc1") is True


def test_is_prerelease_false_for_release() -> None:
    g = _load()
    assert g.is_prerelease("1.0.0") is False


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


def test_assert_versions_match_accepts_prerelease_tag() -> None:
    # A pre-release tag validates against the release core of pyproject/VERSION.
    g = _load()
    g.assert_versions_match("1.2.0-rc1", "1.2.0", "1.2.0")  # no raise


def test_assert_versions_match_raises_on_mismatch() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.assert_versions_match("1.2.0", "1.2.1", "1.2.0")


def test_assert_versions_match_raises_on_prerelease_core_mismatch() -> None:
    # The rc suffix is tolerated, but a mismatched *core* still fails loud.
    g = _load()
    with pytest.raises(SystemExit):
        g.assert_versions_match("1.3.0-rc1", "1.2.0", "1.2.0")


def test_emit_github_output_writes_when_env_set(tmp_path, monkeypatch) -> None:
    g = _load()
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    g.emit_github_output("prerelease", "true")
    assert out.read_text() == "prerelease=true\n"


def test_emit_github_output_noop_when_env_absent(monkeypatch) -> None:
    g = _load()
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    g.emit_github_output("prerelease", "true")  # no raise, nothing written


def test_main_ok_against_repo_files() -> None:
    # The repo's pyproject.toml and VERSION agree; tag must match them.
    g = _load()
    version = g.read_version_file(REPO_ROOT / "VERSION")
    g.main(["prog", f"v{version}"])  # no raise


def test_main_prerelease_tag_ok_and_flags_output(tmp_path, monkeypatch) -> None:
    # A pre-release tag against the repo files passes and reports prerelease=true.
    g = _load()
    version = g.read_version_file(REPO_ROOT / "VERSION")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    g.main(["prog", f"v{version}-rc1"])  # no raise
    assert out.read_text() == "prerelease=true\n"


def test_main_release_tag_flags_output_false(tmp_path, monkeypatch) -> None:
    g = _load()
    version = g.read_version_file(REPO_ROOT / "VERSION")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    g.main(["prog", f"v{version}"])  # no raise
    assert out.read_text() == "prerelease=false\n"


def test_main_usage_error_without_tag() -> None:
    g = _load()
    with pytest.raises(SystemExit):
        g.main(["prog"])
