"""components/ is NOT part of mqlab (epic .github#294 spec §5.1).

Guest components are standalone uv projects at their own quality tier: they never
import mqlab, mqlab never imports them, mqlab's wheel never ships them, and mqlab's
root tooling (ruff, ansible-lint) never judges them. These guards turn that boundary
from a convention into a tested invariant: the #293 failure began with mqlab's py314
ruff rules rewriting guest code that ran on another interpreter.
"""

from __future__ import annotations

import ast
import tomllib
from typing import TYPE_CHECKING

import pytest
import yaml

from mqlab.paths import components_dir, repo_root

if TYPE_CHECKING:
    from pathlib import Path

ROOT = repo_root()


def _imports(source: str) -> set[str]:
    """Top-level package names a module imports (absolute imports only)."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def _component_packages(root: Path) -> set[str]:
    """Import names of every component under ``root`` (its ``src/<pkg>`` dirs)."""
    names: set[str] = set()
    for pyproject in root.glob("*/pyproject.toml"):
        src = pyproject.parent / "src"
        if src.is_dir():
            names |= {p.name for p in src.iterdir() if p.is_dir()}
    return names


def _violations_mqlab_imports_component(mqlab_src: Path, components: Path) -> dict[str, set[str]]:
    packages = _component_packages(components)
    bad: dict[str, set[str]] = {}
    for py in sorted(mqlab_src.rglob("*.py")):
        hits = _imports(py.read_text()) & packages
        if hits:
            bad[str(py)] = hits
    return bad


def _violations_component_imports_mqlab(components: Path) -> list[str]:
    return [str(p) for p in sorted(components.rglob("*.py")) if "mqlab" in _imports(p.read_text())]


# --- the invariants, on the real tree -------------------------------------------------


def test_mqlab_never_imports_a_component():
    assert _violations_mqlab_imports_component(ROOT / "src" / "mqlab", components_dir()) == {}


def test_components_never_import_mqlab():
    assert _violations_component_imports_mqlab(components_dir()) == []


def test_mqlab_wheel_packages_only_src_mqlab():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/mqlab"]


def test_root_ruff_excludes_components():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "components" in cfg["tool"]["ruff"]["extend-exclude"]


def test_ansible_lint_excludes_components():
    cfg = yaml.safe_load((ROOT / ".ansible-lint.yml").read_text())
    assert "components/" in cfg["exclude_paths"]


def test_root_pytest_and_coverage_never_reach_components():
    """vrg-validate's pytest runs testpaths with --cov=src: components stay outside both."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


# --- the guards actually bite (synthetic trees) ----------------------------------------


@pytest.fixture
def fake_tree(tmp_path: Path) -> tuple[Path, Path]:
    mqlab_src = tmp_path / "src" / "mqlab"
    mqlab_src.mkdir(parents=True)
    comp = tmp_path / "components" / "demo"
    (comp / "src" / "demopkg").mkdir(parents=True)
    (comp / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (comp / "src" / "demopkg" / "__init__.py").write_text("")
    return mqlab_src, tmp_path / "components"


@pytest.mark.parametrize(
    "line",
    [
        "import demopkg",
        "import demopkg.sub as s",
        "from demopkg import x",
        "from demopkg.sub import y",
    ],
)
def test_guard_catches_mqlab_importing_a_component(fake_tree, line):
    mqlab_src, components = fake_tree
    (mqlab_src / "cli.py").write_text(f"{line}\n")
    assert _violations_mqlab_imports_component(mqlab_src, components) == {
        str(mqlab_src / "cli.py"): {"demopkg"}
    }


@pytest.mark.parametrize(
    "line", ["import mqlab", "from mqlab import paths", "from mqlab.dr import x"]
)
def test_guard_catches_a_component_importing_mqlab(fake_tree, line):
    _mqlab_src, components = fake_tree
    bad = components / "demo" / "src" / "demopkg" / "mod.py"
    bad.write_text(f"{line}\n")
    assert _violations_component_imports_mqlab(components) == [str(bad)]


def test_guard_ignores_relative_imports(fake_tree):
    _mqlab_src, components = fake_tree
    (components / "demo" / "src" / "demopkg" / "mod.py").write_text("from . import mqlab\n")
    assert _violations_component_imports_mqlab(components) == []


def test_component_without_src_contributes_no_packages(tmp_path):
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "pyproject.toml").write_text("")
    assert _component_packages(tmp_path) == set()
