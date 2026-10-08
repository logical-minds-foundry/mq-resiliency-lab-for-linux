"""A throwaway repo tree for exercising the RHEL DVD shell scripts (#1395).

The scripts resolve their sources from the MAIN worktree's ``build/state/`` (via
``git rev-parse --git-common-dir``) and the catalog relative to themselves, so a test
builds a minimal git repo holding copies of the scripts, a chosen catalog and fake ISO
files. ``stage-iso-into-pool.sh`` is replaced by a stub that logs its arguments, so no
test ever touches the libvirt pool; push-rhel-iso.sh gets a fake ``gcloud`` on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mqlab.versions import load_catalog

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "lab" / "versions.yaml"
_SCRIPTS = (
    "scripts/push-rhel-iso.sh",
    "lab/scripts/stage-rhel-iso.sh",
    "lab/scripts/rhel-catalog-isos.sh",
)

# Logs "<src> <dest>" per call; fails for any ISO whose name is in POOL_FAIL_ON.
_POOL_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$1" "$2" >> "$POOL_LOG"
case " ${POOL_FAIL_ON:-} " in *" $(basename "$2") "*) exit 1 ;; esac
exit 0
"""

# A catalog with two RHEL majors (the shape T10, #1286, introduces) plus a non-RHEL
# family that also names an iso, which must NOT be picked up.
TWO_MAJORS = """\
# a header comment
os:
  ubuntu:
    24: { base_box: some/ubuntu, iso: not-rhel.iso }
  rhel:
    # comment inside the family
    9: { base_box: b/one, point: "1.1", iso: alpha-dvd.iso, arch: x86_64 }
    10: { base_box: b/two, iso: "beta-dvd.iso", ibm_support: { status: s, source: "u?a,b" } }  # c

roles: {}
"""


def catalog_isos() -> list[str]:
    """Every os.rhel.<major>.iso in the committed catalog, per mqlab.versions."""
    oses = load_catalog(CATALOG).oses
    return [e.iso for ref, e in oses.items() if ref.family == "rhel" and e.iso is not None]


def make_tree(tmp_path: Path, catalog: str | None = None) -> Path:
    """Build the repo tree; ``catalog`` is the versions.yaml text (default: the real one)."""
    root = tmp_path / "repo"
    for rel in _SCRIPTS:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dest)
    pool = root / "lab" / "scripts" / "stage-iso-into-pool.sh"
    pool.write_text(_POOL_STUB)
    pool.chmod(0o755)
    versions = root / "lab" / "versions.yaml"
    if catalog is None:
        shutil.copy2(CATALOG, versions)
    else:
        versions.write_text(catalog)
    (root / "build" / "state").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)  # noqa: S607
    return root


def add_iso(root: Path, name: str, size: int = 4096) -> Path:
    """Drop a fake ISO of ``size`` bytes into the tree's build/state/."""
    path = root / "build" / "state" / name
    path.write_bytes(b"x" * size)
    return path
