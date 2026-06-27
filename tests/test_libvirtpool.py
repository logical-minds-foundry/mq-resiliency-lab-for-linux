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
# parse_pool_states / pool_state — the live-state read that makes ensure re-run-safe
# --------------------------------------------------------------------------- #
POOL_LIST = (
    " Name           State      Autostart\n"
    "-------------------------------------\n"
    " default        active     yes\n"
    " mqlab-images   inactive   no\n"
)


def test_parse_pool_states_reads_name_and_state() -> None:
    states = libvirtpool.parse_pool_states(POOL_LIST)
    assert states == {"default": "active", "mqlab-images": "inactive"}


def test_parse_pool_states_skips_short_and_header_rows() -> None:
    # A malformed single-column row is skipped (covers the len(parts) < 2 guard).
    states = libvirtpool.parse_pool_states("Name State\n----\nlonely\n default active\n")
    assert states == {"default": "active"}


def test_pool_state_absent_when_not_listed() -> None:
    assert libvirtpool.pool_state({}, "mqlab-images") == libvirtpool.ABSENT


def test_pool_state_active_and_inactive() -> None:
    states = {"mqlab-images": "active", "other": "inactive"}
    assert libvirtpool.pool_state(states, "mqlab-images") == libvirtpool.ACTIVE
    assert libvirtpool.pool_state(states, "other") == libvirtpool.INACTIVE


# --------------------------------------------------------------------------- #
# pool_ensure_steps — emits ONLY the lifecycle steps the current state needs
# --------------------------------------------------------------------------- #
def _subcmds(steps) -> list[str]:
    # the virsh subcommand of each step (e.g. pool-define-as) — the position after
    # the qemu:///system connect URI in _VIRSH.
    return [s.command.argv[s.command.argv.index("qemu:///system") + 1] for s in steps]


def test_absent_pool_runs_full_lifecycle() -> None:
    steps = libvirtpool.pool_ensure_steps(
        "mqlab-images", Path("/data/images"), state=libvirtpool.ABSENT
    )
    virsh = libvirtpool._VIRSH
    assert [s.command.argv for s in steps] == [
        [*virsh, "pool-define-as", "mqlab-images", "dir", "--target", "/data/images"],
        [*virsh, "pool-build", "mqlab-images"],
        [*virsh, "pool-start", "mqlab-images"],
        [*virsh, "pool-autostart", "mqlab-images"],
    ]


def test_absent_is_the_default_state() -> None:
    # No state kwarg -> ABSENT -> full lifecycle (back-compat default).
    steps = libvirtpool.pool_ensure_steps("mqlab-images", Path("/data/images"))
    assert _subcmds(steps) == ["pool-define-as", "pool-build", "pool-start", "pool-autostart"]


def test_inactive_pool_skips_define() -> None:
    # Defined but not running: build + start + autostart, NO re-define.
    steps = libvirtpool.pool_ensure_steps(
        "mqlab-images", Path("/data/images"), state=libvirtpool.INACTIVE
    )
    assert _subcmds(steps) == ["pool-build", "pool-start", "pool-autostart"]


def test_active_pool_only_reasserts_autostart() -> None:
    # Already active: only autostart (a safe no-op re-assert) — never re-build/start
    # an active pool, which can exit non-zero and halt a --from resume.
    steps = libvirtpool.pool_ensure_steps(
        "mqlab-images", Path("/data/images"), state=libvirtpool.ACTIVE
    )
    assert _subcmds(steps) == ["pool-autostart"]


def test_pool_ensure_steps_are_labeled() -> None:
    steps = libvirtpool.pool_ensure_steps("mqlab-images", Path("/data/images"))
    labels = [s.label for s in steps]
    assert labels == [
        "mqlab-images pool define",
        "mqlab-images pool build",
        "mqlab-images pool start",
        "mqlab-images pool autostart",
    ]
