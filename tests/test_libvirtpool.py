"""Tests for the env-aware libvirt image-pool helper (#376).

On the cloud box libvirt stores VM images on the small boot disk
(/var/lib/libvirt/images), which fills and pauses the guests (werror=stop on
ENOSPC). The fix redirects the pool onto the big data disk — but only where
build/ lives on a real block device. On local (Lima) build/ is a virtiofs
host-mount where qcow2 is unsafe, so the default pool is left untouched.

This module is PURE: the fstype probe is injected, so detection, the target
resolution, and the virsh step builder are all unit-tested at 100% branch
coverage with no subprocess.
"""

from __future__ import annotations

from pathlib import Path

from mqlab import libvirtpool


# --------------------------------------------------------------------------- #
# is_host_mount — the fstype classifier
# --------------------------------------------------------------------------- #
def test_host_mount_fstypes_are_host_mounts() -> None:
    for fstype in ("9p", "virtiofs", "fuse.virtiofs"):
        assert libvirtpool.is_host_mount(fstype) is True


def test_real_disk_fstypes_are_not_host_mounts() -> None:
    for fstype in ("ext4", "xfs", "btrfs"):
        assert libvirtpool.is_host_mount(fstype) is False


# --------------------------------------------------------------------------- #
# pool_override — the env-aware decision (fstype probe injected)
# --------------------------------------------------------------------------- #
def test_pool_override_none_on_host_mount() -> None:
    # Local (Lima) build/ is virtiofs -> leave the default pool alone.
    name = libvirtpool.pool_override(Path("/repo"), probe_fstype=lambda p: "virtiofs")
    assert name is None


def test_pool_override_uses_dedicated_pool_on_real_disk() -> None:
    # Cloud build/ is ext4 on a real block device -> use the dedicated pool.
    name = libvirtpool.pool_override(Path("/repo"), probe_fstype=lambda p: "ext4")
    assert name == libvirtpool.POOL_NAME


def test_pool_override_probes_the_build_root() -> None:
    # The fstype probe must target the repo's build/ root (where images land),
    # not the repo root itself.
    seen: list[Path] = []

    def probe(path: Path) -> str:
        seen.append(path)
        return "ext4"

    libvirtpool.pool_override(Path("/repo"), probe_fstype=probe)
    assert seen == [Path("/repo") / "build"]


# --------------------------------------------------------------------------- #
# images_target — the pool directory on the real disk
# --------------------------------------------------------------------------- #
def test_images_target_is_a_local_work_bucket_subdir() -> None:
    # Images are local + huge + regenerable on rebuild -> the local work bucket,
    # resolved via the build helpers (never a hardcoded build/<X> path).
    target = libvirtpool.images_target(Path("/repo"), bucket=lambda b, r: r / "build" / b)
    assert target == Path("/repo") / "build" / "work" / "libvirt-images"


# --------------------------------------------------------------------------- #
# pool_ensure_steps — the idempotent virsh define/build/start/autostart steps
# --------------------------------------------------------------------------- #
def test_pool_ensure_steps_emit_the_virsh_lifecycle() -> None:
    steps = libvirtpool.pool_ensure_steps("mqlab-images", Path("/data/images"))
    argvs = [s.command.argv for s in steps]
    virsh = libvirtpool._VIRSH
    assert argvs == [
        [*virsh, "pool-define-as", "mqlab-images", "dir", "--target", "/data/images"],
        [*virsh, "pool-build", "mqlab-images"],
        [*virsh, "pool-start", "mqlab-images"],
        [*virsh, "pool-autostart", "mqlab-images"],
    ]


def test_pool_ensure_steps_are_labeled() -> None:
    steps = libvirtpool.pool_ensure_steps("mqlab-images", Path("/data/images"))
    labels = [s.label for s in steps]
    assert labels == [
        "mqlab-images pool define",
        "mqlab-images pool build",
        "mqlab-images pool start",
        "mqlab-images pool autostart",
    ]
