#!/usr/bin/env python3
"""Fail loud unless the release tag, pyproject.toml version, and VERSION agree.

Invoked by .github/workflows/release.yml before building the tarball:
    python tools/release_version_guard.py "$GITHUB_REF_NAME"

Pre-release tags (e.g. ``v1.0.0-rc1``) are accepted for safe iteration: the
guard compares the tag's *release core* (``1.0.0``) against pyproject.toml and
VERSION, so an rc tag exercises the whole pipeline without needing a throwaway
version bump. The tie between tag, pyproject.toml, and VERSION is preserved — a
mismatch in the core still fails loud. Whether the tag is a pre-release is
surfaced to the workflow via the ``prerelease`` GitHub Actions output so the
published Release can be flagged pre-release (and never marked "Latest").
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


def parse_tag_version(ref: str) -> str:
    """'v1.2.0' -> '1.2.0'; a bare '1.2.0' passes through unchanged."""
    return ref[1:] if ref.startswith("v") else ref


def base_version(version: str) -> str:
    """Release core of a version, dropping any pre-release suffix.

    '1.0.0-rc1' -> '1.0.0'; '1.0.0' -> '1.0.0'. The suffix is everything from
    the first '-' onward (git-style pre-release tags like 'v1.0.0-rc1').
    """
    return version.split("-", 1)[0]


def is_prerelease(version: str) -> bool:
    """True when the version carries a pre-release suffix ('1.0.0-rc1')."""
    return "-" in version


def read_pyproject_version(path: Path) -> str:
    return tomllib.loads(path.read_text())["project"]["version"]


def read_version_file(path: Path) -> str:
    return path.read_text().strip()


def assert_versions_match(tag_version: str, pyproject_version: str, version_file: str) -> None:
    core = base_version(tag_version)
    if not core == pyproject_version == version_file:
        raise SystemExit(
            "release version mismatch: "
            f"tag={tag_version!r} (core {core!r}) "
            f"pyproject={pyproject_version!r} VERSION={version_file!r}"
        )


def emit_github_output(name: str, value: str) -> None:
    """Append ``name=value`` to $GITHUB_OUTPUT when running under Actions.

    A no-op locally (no GITHUB_OUTPUT set) — this is CI plumbing, not a gate,
    so its absence off-CI is expected, not a swallowed failure.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{name}={value}\n")


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
    prerelease = is_prerelease(tag_version)
    emit_github_output("prerelease", "true" if prerelease else "false")
    kind = "pre-release" if prerelease else "release"
    print(f"ok: {kind} version {tag_version}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
