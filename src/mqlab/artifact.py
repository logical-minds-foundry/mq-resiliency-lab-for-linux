"""MQ tarball acquisition (#266): cache -> download -> verify.

A pinned (version x arch) tarball is immutable, so it is safely cached. We never
fail just because a tarball is absent — MQ Advanced for Developers is downloadable
for every arch we use; `fetch` populates the cache on a miss. The sibling `.sha256`
is verified on every acquire (recorded on first acquire, since IBM publishes no
checksum file beside the tarballs): integrity, not version-reconciliation.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
from typing import TYPE_CHECKING

from mqlab.manifest import tarball_name

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from mqlab.versions import BoxEntry

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
    """Verify a cached tarball against its `.sha256` sidecar.

    IBM publishes no checksum file beside the Developer tarballs on the CDN, so the
    lab's trust model is record-on-first-acquire, verify-on-every-later-use (the same
    as scripts/fetch-mq.sh). A tarball with no sidecar (placed in the cache by hand)
    is never silently passed: its digest is recorded now, loudly, so every later
    acquire verifies against it (#1407).
    """
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {path.name}\n")
        sys.stderr.write(
            f"NOTICE: {path.name} had no .sha256 sidecar; recorded {digest} "
            "(trust-on-first-use; verified on every later acquire)\n"
        )
        return
    expected = sidecar.read_text().split()[0].strip().lower()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {path.name}: {actual} != {expected}")


def ensure_mq_tarballs_for_boxes(
    boxes: set[BoxEntry],
    mq_version: str,
    build_mq_dir: Path,
    *,
    fetch: Callable[[str, Path], None],
) -> list[Path]:
    """Ensure the MQ tarball for each given box is present + valid in the cache.

    The stack/commons bootstrap path (#350) resolves the box set through the version
    layer (versions.node_boxes) and passes it here; this only acquires it (fetch on a
    miss, verify the sha256 sidecar). One place to fetch + verify, no silent fallback.
    Boxes that share a tarball (same family + arch) resolve to one cached file.
    """
    paths: list[Path] = []
    names = sorted({tarball_name(mq_version, entry) for entry in boxes})
    for name in names:
        dest = build_mq_dir / name
        if not dest.exists():
            fetch(name, dest)  # raises on failure (no silent fallback)
        _verify_sha256(dest)
        paths.append(dest)
    return paths
