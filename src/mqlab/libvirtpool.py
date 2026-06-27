"""Env-aware libvirt image-pool helper (#376) — pure, fully unit-testable.

Root cause (confirmed live in #375): on the cloud box libvirt stores VM images at
the default pool `/var/lib/libvirt/images` on the 29 GB boot disk, which fills to
100% during a full HADR bring-up. QEMU's `werror=stop` then pauses the guests on
ENOSPC ("No route to host"), while the 196 GB `/vergil` data volume sits idle.

The fix redirects libvirt's storage onto the real data disk — but ONLY where it
is safe and needed:

  * Cloud:  build/ lives on /vergil (ext4 on a real block device). qcow2 is safe
            there, and the boot disk is the constraint -> use a dedicated pool.
  * Local:  build/ is a virtiofs/9p host-mount (Lima). qcow2 on virtiofs/9p is
            unsafe (file-locking via virtlockd, O_DIRECT/cache=none semantics),
            and local has no disk problem -> leave the libvirt default pool.

Detection keys off the fstype of the repo's build/ root. This module is PURE: the
fstype probe and the build-bucket resolver are injected, so detection, the target
resolution, and the virsh step builder are exercised with no subprocess at 100%
branch coverage. The host-side I/O (findmnt, virsh) lives in cli.py, mirroring how
secrets / prereqs / the vagrant env are handled by the sequencer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mqlab.orchestrator import CommandStep
from mqlab.runner import Command

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The dedicated libvirt dir pool name (used in the Vagrantfile via MQLAB_LIBVIRT_POOL).
POOL_NAME = "mqlab-images"

# Host-mount filesystems where build/ is a guest<->host share (Lima virtiofs/9p),
# NOT a real block device. qcow2 on these is unsafe -> never override the pool.
HOST_MOUNT_FSTYPES = frozenset({"9p", "virtiofs", "fuse.virtiofs"})

# virsh, run as the lab host operator (mirrors cli.py / phases.py's _VIRSH).
_VIRSH = ["virsh", "-c", "qemu:///system"]


def is_host_mount(fstype: str) -> bool:
    """True iff `fstype` names a guest<->host share (virtiofs/9p), not a real disk."""
    return fstype in HOST_MOUNT_FSTYPES


def pool_override(repo: Path, *, probe_fstype: Callable[[Path], str]) -> str | None:
    """The libvirt pool name to override with, or None to keep the default.

    Probes the fstype of the repo's build/ root: a host-mount (local/Lima) keeps
    libvirt's default pool (return None); a real disk (cloud) uses the dedicated
    pool. The fstype probe is injected so the decision is unit-tested without
    touching the filesystem.
    """
    build_root = repo / "build"
    if is_host_mount(probe_fstype(build_root)):
        return None
    return POOL_NAME


def images_target(repo: Path, *, bucket: Callable[[str, Path], Path]) -> Path:
    """The pool's backing directory on the real disk: the local work/ bucket's
    libvirt-images subdir.

    Images are local, huge, and regenerated on every rebuild (NOT shared or
    irreplaceable live-lab state), so they belong in the local `work` bucket. The
    bucket path is resolved via the injected build helper — never a hardcoded
    build/<X> path (#286).
    """
    return bucket("work", repo) / "libvirt-images"


def pool_ensure_steps(name: str, target: Path) -> list[CommandStep]:
    """The idempotent virsh steps that ensure a dir pool exists at `target`.

    Mirrors the net phase's define/autostart/start triple, extended with the
    pool-build step a dir pool needs. virsh treats a re-define / re-build /
    re-start of an existing pool as a no-op (or a tolerated already-exists), so
    the sequence is idempotent. Glass-box: each runs through the step runner like
    every other host-side op.
    """
    return [
        CommandStep(
            f"{name} pool define",
            Command([*_VIRSH, "pool-define-as", name, "dir", "--target", str(target)]),
        ),
        CommandStep(f"{name} pool build", Command([*_VIRSH, "pool-build", name])),
        CommandStep(f"{name} pool start", Command([*_VIRSH, "pool-start", name])),
        CommandStep(f"{name} pool autostart", Command([*_VIRSH, "pool-autostart", name])),
    ]
