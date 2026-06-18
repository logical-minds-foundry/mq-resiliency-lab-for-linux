"""MQ tarball acquisition (#266): cache -> download -> verify.

A pinned (version x arch) tarball is immutable, so it is safely cached. We never
fail just because a tarball is absent — MQ Advanced for Developers is downloadable
for every arch we use; `fetch` populates the cache on a miss. The sibling `.sha256`
(when present) is verified on acquire: integrity, not version-reconciliation.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from mqlab.manifest import setup_platforms, tarball_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _verify_sha256(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return  # no checksum to verify against — immutability is the guarantee
    expected = sidecar.read_text().split()[0].strip().lower()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {path.name}: {actual} != {expected}")


def ensure_mq_tarballs(
    setup: str,
    mq_version: str,
    build_mq_dir: Path,
    *,
    fetch: Callable[[str, Path], None],
) -> list[Path]:
    """Ensure the MQ tarball for each distinct platform in `setup` is present + valid."""
    paths: list[Path] = []
    for platform in sorted(setup_platforms(setup)):
        name = tarball_name(mq_version, platform)
        dest = build_mq_dir / name
        if not dest.exists():
            fetch(name, dest)  # raises on failure (no silent fallback)
        _verify_sha256(dest)
        paths.append(dest)
    return paths
