#!/usr/bin/env python3
"""Fail loud unless the release tag, pyproject.toml version, and VERSION agree.

Invoked by .github/workflows/release.yml before building the tarball:
    python tools/release_version_guard.py "$GITHUB_REF_NAME"
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


def parse_tag_version(ref: str) -> str:
    """'v1.2.0' -> '1.2.0'; a bare '1.2.0' passes through unchanged."""
    return ref[1:] if ref.startswith("v") else ref


def read_pyproject_version(path: Path) -> str:
    return tomllib.loads(path.read_text())["project"]["version"]


def read_version_file(path: Path) -> str:
    return path.read_text().strip()


def assert_versions_match(tag_version: str, pyproject_version: str, version_file: str) -> None:
    if not tag_version == pyproject_version == version_file:
        raise SystemExit(
            "release version mismatch: "
            f"tag={tag_version!r} pyproject={pyproject_version!r} VERSION={version_file!r}"
        )


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: release_version_guard.py <tag>")
    root = Path(__file__).resolve().parent.parent
    tag_version = parse_tag_version(argv[1])
    assert_versions_match(
        tag_version,
        read_pyproject_version(root / "pyproject.toml"),
        read_version_file(root / "VERSION"),
    )
    print(f"ok: release version {tag_version}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
