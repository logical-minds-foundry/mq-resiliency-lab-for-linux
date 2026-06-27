"""MQ tarball acquisition (#266): cache -> download -> verify.

A pinned (version x arch) tarball is immutable, so it is safely cached. We never
fail just because a tarball is absent — MQ Advanced for Developers is downloadable
for every arch we use; `fetch` populates the cache on a miss. The sibling `.sha256`
(when present) is verified on acquire: integrity, not version-reconciliation.
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
from typing import TYPE_CHECKING

from mqlab.manifest import tarball_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# IBM MQ Advanced for Developers is a no-charge, NO-AUTH public download — so a
# credential-less box (e.g. the anonymous bootstrap identity, #291) can fetch it.
MQ_CDN_BASE = "https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv"


def mq_tarball_url(name: str) -> str:
    return f"{MQ_CDN_BASE}/{name}"


def _stream_download(url: str, dest_part: Path) -> None:  # pragma: no cover - real network I/O
    with urllib.request.urlopen(url) as resp, dest_part.open("wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh)


def download_mq_tarball(
    name: str, dest: Path, *, download: Callable[[str, Path], None] = _stream_download
) -> None:
    """Fetch an MQ-for-Developers tarball from IBM's no-auth public CDN into `dest`,
    atomically (via a .part), recording a sha256 sidecar for later cache-integrity
    checks. Credential-less by design (#276) — this is what a no-git box can fetch."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    download(mq_tarball_url(name), part)
    part.rename(dest)
    sidecar = dest.with_name(dest.name + ".sha256")
    if not sidecar.exists():
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {dest.name}\n")


def _verify_sha256(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return  # no checksum to verify against — immutability is the guarantee
    expected = sidecar.read_text().split()[0].strip().lower()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {path.name}: {actual} != {expected}")


def ensure_mq_tarballs_for_platforms(
    platforms: set[str],
    mq_version: str,
    build_mq_dir: Path,
    *,
    fetch: Callable[[str, Path], None],
) -> list[Path]:
    """Ensure the MQ tarball for each given platform is present + valid in the cache.

    The stack/commons bootstrap path (#350) resolves the platform set (host-resolved,
    #276) and passes it here; this only acquires it (fetch on a miss, verify the
    sha256 sidecar). One place to fetch + verify, no silent fallback.
    """
    paths: list[Path] = []
    for platform in sorted(platforms):
        name = tarball_name(mq_version, platform)
        dest = build_mq_dir / name
        if not dest.exists():
            fetch(name, dest)  # raises on failure (no silent fallback)
        _verify_sha256(dest)
        paths.append(dest)
    return paths
