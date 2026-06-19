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
    raise RuntimeError("repo root not found above mqlab package")  # pragma: no cover


def runs_dir() -> Path:
    """Where transcripts are written — always under the gitignored build/ tree."""
    return repo_root() / "build" / "runs"


def reports_dir() -> Path:
    """Where run-report bundles are written — under the gitignored build/ tree."""
    return repo_root() / "build" / "reports"


def manifests_root() -> Path:
    """Committed version-manifest source tree at the repo root (#266)."""
    return repo_root() / "manifests"


def selection_state_path(setup: str) -> Path:
    """Where a live build's resolved manifest selection is pinned (gitignored build/)."""
    return repo_root() / "build" / "manifests" / f"{setup}.yaml"


def resolved_topology_path() -> Path:
    """Where the host-resolved topology is rendered for the Vagrantfile (#276)."""
    return repo_root() / "build" / "lab" / "topology.resolved.yaml"


def lab_script(name: str) -> Path:
    """Absolute path to a script under lab/scripts/."""
    return repo_root() / "lab" / "scripts" / name


def lab_network(name: str) -> Path:
    """Absolute path to a network definition under lab/networks/ (e.g. net-define)."""
    return repo_root() / "lab" / "networks" / f"{name}.xml"
