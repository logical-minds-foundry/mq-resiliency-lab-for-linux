from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

EXCLUDED = [
    ".gitattributes",
    ".github/",
    ".claude/",
    ".vergil/",
    ".worktrees/",
    ".superpowers/",
    "vergil.toml",
    "tests/",
]
INCLUDED = ["src/mqlab/", "ansible/", "lab/", "manifests/", "pyproject.toml", "VERSION"]


def test_gitattributes_declares_every_exclusion() -> None:
    """Container-safe: each dev-only path is marked export-ignore."""
    lines = GITATTRIBUTES.read_text().splitlines()
    for path in EXCLUDED:
        assert any(
            line.split()[0] == path and "export-ignore" in line
            for line in lines
            if line.strip() and not line.startswith("#")
        ), f"{path} is not marked export-ignore in .gitattributes"


def _archived_names() -> set[str]:
    """Names in `git archive` of the worktree, prefix stripped. Honors the
    working-tree .gitattributes via --worktree-attributes. Decodes the archive
    in-memory — writes no file (the build/ layout is managed via `mqlab build`,
    never hardcoded). Raises on any git failure so the caller can skip in
    git-less environments."""
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git not found on PATH")
    out = subprocess.run(
        [git, "archive", "--worktree-attributes", "--prefix=pkg/", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tar:
        return {m.name.removeprefix("pkg/") for m in tar.getmembers()}


def _archive_or_skip() -> set[str]:
    try:
        return _archived_names()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"git archive unavailable in this environment: {exc}")


def test_archive_excludes_dev_only_paths() -> None:
    names = _archive_or_skip()
    for path in EXCLUDED:
        assert not any(n == path or n.startswith(path) for n in names), (
            f"{path} leaked into archive"
        )


def test_archive_includes_product_paths() -> None:
    names = _archive_or_skip()
    for path in INCLUDED:
        assert any(n == path or n.startswith(path) for n in names), f"{path} missing from archive"
