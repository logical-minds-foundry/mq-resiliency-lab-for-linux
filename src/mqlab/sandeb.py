"""SAN install-half .deb acquisition (#796): cache -> apt-get download.

The two SAN target VMs (san-a / san-b) stay on the host-resolved base Ubuntu box
— baking a fat `san-ubuntu` box was rejected as disproportionate for two
payload-light VMs (decision `logical-minds-foundry/.github#108`). To keep a cold
rebuild fast without a fat box, we pre-fetch the SAN install-half packages into
the shared cache so the drbd-san / iscsi-target roles install them from a local
mgmt-network copy instead of a ~100 MB internet pull.

This mirrors the MQ-tarball path (`artifact.py`): host-side fetch into the
nuke-safe `cache/` bucket, injectable I/O so tests never touch apt. The one
difference is the failure posture. An MQ-tarball miss is fatal (there is no other
way to install MQ), but a SAN-deb miss is **not**: the roles carry a network
`apt install` fallback (`docs/development/san-deb-cache.md`), so a package we
cannot pre-fetch simply installs online on the target. Pre-caching is therefore
best-effort-but-loud — a download failure is reported, never hidden, and the
bootstrap continues on the fallback rather than aborting.

`linux-modules-extra` is the one kernel-coupled package (it ships the in-tree
DRBD module for a specific kernel), so it is keyed by kernel version. The version
we cache is the controller's running kernel; if the SAN base box has since moved
to a newer kernel, the cache misses on that one deb and the role network-installs
it — self-healing, and a re-run re-caches the new kernel's deb under its own name.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The kernel-independent SAN install-half packages, cached verbatim: drbd-utils is
# the DRBD userland (drbd-san role), targetcli-fb the LIO admin tool (iscsi-target
# role). Both SAN roles run on each SAN target, so the one cache holds both.
SAN_KERNEL_INDEPENDENT_PKGS: tuple[str, ...] = ("drbd-utils", "targetcli-fb")


def kernel_modules_pkg(kernel: str) -> str:
    """The kernel-keyed modules-extra package name for a kernel release string.

    e.g. "6.8.0-106-generic" -> "linux-modules-extra-6.8.0-106-generic". This is the
    package the drbd-san role installs via `linux-modules-extra-{{ ansible_kernel }}`,
    so the cached deb's name must match the target's running kernel to be a cache hit.
    """
    return f"linux-modules-extra-{kernel}"


def san_deb_packages(kernel: str) -> list[str]:
    """The full SAN install-half package set to pre-cache for a target kernel."""
    return [*SAN_KERNEL_INDEPENDENT_PKGS, kernel_modules_pkg(kernel)]


# The drbd-san role records the SAN base box's actual kernel here (delegate_to
# localhost, after a boot) so a later pre-cache keys linux-modules-extra to the
# kernel that really boots — the controller's own kernel drifts from the cloud
# image's and is only the first-rebuild seed (#816).
_TARGET_KERNEL_FILE = ".target-kernel"


def observed_target_kernel(cache_dir: Path) -> str | None:
    """The SAN base box's kernel as observed on a prior rebuild, or None if never seen.

    First rebuild: the base box has not booted, so this is None and the caller seeds
    the pre-cache with the controller's own kernel (a miss → network fallback). Every
    rebuild after: the drbd-san role has recorded the target's `ansible_kernel`, so the
    pre-cache fetches linux-modules-extra for the kernel that actually boots — an
    offline hit, no ~100 MB pull. This is the real self-healing (#816).
    """
    path = cache_dir / _TARGET_KERNEL_FILE
    if not path.is_file():
        return None
    kernel = path.read_text().strip()
    return kernel or None


def cached_deb(cache_dir: Path, pkg: str) -> Path | None:
    """The cached `.deb` for a package name, or None if absent.

    apt names a deb `<pkg>_<version>_<arch>.deb`, so we match on the `<pkg>_` prefix
    — the version/arch tail is whatever apt resolved. The role's offline install
    globs the same way on the target.
    """
    matches = sorted(cache_dir.glob(f"{pkg}_*.deb"))
    return matches[0] if matches else None


def _apt_get_download(pkg: str, dest_dir: Path) -> None:  # pragma: no cover - real apt I/O
    """Fetch a single `.deb` (and nothing else) into dest_dir via `apt-get download`.

    `apt-get download` pulls the package archive without installing it, writing the
    `.deb` into the current directory — so we run it with cwd=dest_dir. It downloads
    the controller's native architecture, which matches the host-resolved SAN targets.
    Raises CalledProcessError on failure (caller reports it, then relies on the role's
    network fallback)."""
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["apt-get", "download", pkg],  # noqa: S607 - apt-get resolved via PATH by design
        cwd=dest_dir,
        check=True,
        capture_output=True,
    )


def ensure_san_debs(
    cache_dir: Path,
    kernel: str,
    *,
    download: Callable[[str, Path], None] = _apt_get_download,
) -> dict[str, str]:
    """Ensure the SAN install-half debs for a kernel are present in the cache.

    For each package: a matching cached deb is left as-is (immutable per
    name+version); a miss is fetched via `download`. A fetch that fails — or that
    produces no deb — is reported loudly to stderr and recorded as "unavailable"
    rather than raised: the drbd-san / iscsi-target roles network-install anything
    absent from the cache, so a pre-cache miss degrades to a slower install, never a
    broken bootstrap (unlike the MQ tarball, which has no fallback).

    Returns a per-package status map ("cached" | "downloaded" | "unavailable") so the
    caller can surface what was pre-fetched vs. left to the network.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    for pkg in san_deb_packages(kernel):
        if cached_deb(cache_dir, pkg) is not None:
            results[pkg] = "cached"
            continue
        try:
            download(pkg, cache_dir)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(
                f"warning: could not pre-cache SAN deb {pkg!r} "
                f"({exc}); the target will install it from the network instead.",
                file=sys.stderr,
            )
            results[pkg] = "unavailable"
            continue
        if cached_deb(cache_dir, pkg) is None:
            print(
                f"warning: `apt-get download {pkg}` produced no .deb; "
                "the target will install it from the network instead.",
                file=sys.stderr,
            )
            results[pkg] = "unavailable"
        else:
            results[pkg] = "downloaded"
    return results
