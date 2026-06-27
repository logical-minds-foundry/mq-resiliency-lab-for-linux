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


# Pool states virsh reports (the State column of `virsh pool-list --all`). A pool
# this code has never created is ABSENT (not in the listing at all).
ACTIVE = "active"
INACTIVE = "inactive"
ABSENT = "absent"


def parse_pool_states(text: str) -> dict[str, str]:
    """Parse `virsh pool-list --all` output -> {pool_name: state}.

    Same Name / State / Autostart / Persistent column layout as
    `virsh net-list --all` (name first, state second), so it parses identically.
    """
    states: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Name") or set(line) <= {"-"}:
            continue
        parts = line.split()
        if len(parts) < 2:  # noqa: PLR2004 - a Name+State row needs two columns
            continue
        states[parts[0]] = parts[1]
    return states


def pool_state(states: dict[str, str], name: str) -> str:
    """The pool's state from a parsed pool listing: ACTIVE / INACTIVE / ABSENT."""
    raw = states.get(name)
    if raw is None:
        return ABSENT
    return ACTIVE if raw == ACTIVE else INACTIVE


def pool_ensure_steps(name: str, target: Path, *, state: str = ABSENT) -> list[CommandStep]:
    """The virsh steps that bring a dir pool at `target` to defined+built+active+
    autostart, emitting ONLY the steps the current `state` still needs.

    Idempotency is by looking before leaping (the #99 pattern), NOT by tolerating
    non-zero exits: `pool-build`/`pool-start` on an already-built/active pool can
    exit non-zero on some libvirt versions, which would fail-loud and halt a
    `--from` resume. So:
      ABSENT   -> define + build + start + autostart (full lifecycle)
      INACTIVE -> build + start + autostart (defined but not running; build is
                  cheap+safe on a dir pool whose directory already exists)
      ACTIVE   -> autostart only (re-asserting autostart is a safe no-op)
    Every genuine error still surfaces (fail-loud). Glass-box: each runs through
    the step runner like every other host-side op.
    """
    steps: list[CommandStep] = []
    if state == ABSENT:
        steps.append(
            CommandStep(
                f"{name} pool define",
                Command([*_VIRSH, "pool-define-as", name, "dir", "--target", str(target)]),
            )
        )
    if state in (ABSENT, INACTIVE):
        steps.append(CommandStep(f"{name} pool build", Command([*_VIRSH, "pool-build", name])))
        steps.append(CommandStep(f"{name} pool start", Command([*_VIRSH, "pool-start", name])))
    steps.append(CommandStep(f"{name} pool autostart", Command([*_VIRSH, "pool-autostart", name])))
    return steps
