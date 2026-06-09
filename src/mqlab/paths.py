"""Filesystem anchors for the lab repo (spec §4.3, §7)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """The repo root — the directory holding pyproject.toml.

    Honors the MQLAB_REPO_ROOT override (set when mqlab runs outside an editable
    checkout); otherwise walks up from this file.
    """
    override = os.environ.get("MQLAB_REPO_ROOT")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repo root (pyproject.toml) not found above the mqlab package")  # pragma: no cover


def runs_dir() -> Path:
    """Where transcripts are written — always under the gitignored build/ tree."""
    return repo_root() / "build" / "runs"


def lab_script(name: str) -> Path:
    """Absolute path to a script under lab/scripts/."""
    return repo_root() / "lab" / "scripts" / name
