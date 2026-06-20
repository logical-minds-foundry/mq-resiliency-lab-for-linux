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


# --- The four-bucket build/ layout (#286). build/ holds exactly these buckets:
#   cache/ shared re-fetchable downloads · state/ shared live-lab facts ·
#   work/ local renders (nuked each rebuild) · temp/ local scratch.
# Python addresses build/ only through these primitives — no bare "build/<name>".
def build_root() -> Path:
    return repo_root() / "build"


def cache(*parts: str) -> Path:
    return build_root().joinpath("cache", *parts)


def state(*parts: str) -> Path:
    return build_root().joinpath("state", *parts)


def work(*parts: str) -> Path:
    return build_root().joinpath("work", *parts)


def temp_dir() -> Path:
    return build_root() / "temp"


def runs_dir() -> Path:
    """Transcripts — under the shared state/ bucket (the lab's audit trail, #286)."""
    return state("runs")


def reports_dir() -> Path:
    """Run-report bundles — shared state/ (audit trail, #286)."""
    return state("reports")


def manifests_root() -> Path:
    """Committed version-manifest source tree at the repo root (#266)."""
    return repo_root() / "manifests"


def selection_state_path(setup: str) -> Path:
    """Manifest selection pin for a live setup — shared state/ (#266, #286)."""
    return state("manifests", f"{setup}.yaml")


def resolved_topology_path() -> Path:
    """Host-resolved topology rendered for the Vagrantfile — local work/ (#276, #286)."""
    return work("lab", "topology.resolved.yaml")


def inventory_path() -> Path:
    """Rendered Ansible inventory — local work/ (#286)."""
    return work("inventory.ini")


def box_versions_path() -> Path:
    """Manifest box-version pins read by the Vagrantfile — local work/ (#266, #286)."""
    return work("box-versions.json")


def mq_cache_dir() -> Path:
    """MQ tarball cache — shared cache/ (re-fetchable downloads, #286)."""
    return cache("mq")


def lab_script(name: str) -> Path:
    """Absolute path to a script under lab/scripts/."""
    return repo_root() / "lab" / "scripts" / name


def lab_network(name: str) -> Path:
    """Absolute path to a network definition under lab/networks/ (e.g. net-define)."""
    return repo_root() / "lab" / "networks" / f"{name}.xml"
