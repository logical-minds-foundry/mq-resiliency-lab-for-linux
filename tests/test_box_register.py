"""lab/boxes/_box-register.sh: keep a REUSE box registered, not re-added (#1248).

The REUSE path used to `vagrant box add --force` from the cache on every run. That
re-unpacked the box and gave its box.img a new mtime, so vagrant-libvirt (which keys
an unversioned box's base volume on that mtime) uploaded the base image into the
libvirt pool again on the next `vagrant up`. The helper stamps each registration with
the identity of the cache it came from and skips the add while that identity holds;
an unstamped registration is adopted only when it is byte-identical to the cache.

These tests source the helper in bash with a fake `vagrant` on PATH (it records each
call and extracts the cache the way `vagrant box add --force` does) and a throwaway
VAGRANT_HOME, so no real vagrant/libvirt is touched.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "_box-register.sh"
_META = b'{"provider":"libvirt","format":"qcow2","virtual_size":20}\n'

# A fake `vagrant box add ... <name> <cache>`: log the argv, then replace the box dir
# (what --force does) with the cache extracted under <name>/0/arm64/libvirt/.
# FAKE_VAGRANT_EMPTY=1 makes the add "succeed" but register nothing.
_FAKE_VAGRANT = """#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$FAKE_VAGRANT_LOG"
[ "${FAKE_VAGRANT_EMPTY:-0}" = 1 ] && exit 0
args=("$@")
name="${args[${#args[@]}-2]}"
cache="${args[${#args[@]}-1]}"
dir="$VAGRANT_HOME/boxes/${name//\\//-VAGRANTSLASH-}"
rm -rf "$dir"
mkdir -p "$dir/0/arm64/libvirt"
tar -xzf "$cache" -C "$dir/0/arm64/libvirt"
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "vagrant"
    fake.write_text(_FAKE_VAGRANT)
    fake.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VAGRANT_HOME": str(tmp_path / "vagrant-home"),
        "FAKE_VAGRANT_LOG": str(tmp_path / "vagrant.log"),
        **extra,
    }


def _bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["bash", "-c", f'set -euo pipefail; . "{HELPER}"; {script}'],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _adds(tmp_path: Path) -> list[str]:
    log = tmp_path / "vagrant.log"
    return log.read_text().splitlines() if log.exists() else []


def _cache(tmp_path: Path, image: bytes = b"box-v1", mtime: int = 1_000) -> Path:
    """A `.box` the way the builders write it: tar.gz of metadata.json + box.img."""
    cache = tmp_path / "obs-ubuntu2404-aarch64.box"
    with tarfile.open(cache, "w:gz") as tar:
        for name, data in (("metadata.json", _META), ("box.img", image)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    os.utime(cache, (mtime, mtime))
    return cache


def _provider(tmp_path: Path, box: str = "obs-ubuntu2404") -> Path:
    return tmp_path / "vagrant-home" / "boxes" / box / "0" / "arm64" / "libvirt"


def _reuse(cache: Path, manifest: str = "h1") -> str:
    return (
        f'id="$(box_reg_identity "{cache}" {manifest})"; '
        f'box_reuse_register obs-ubuntu2404 "{cache}" "$id" --provider libvirt --force '
        f'obs-ubuntu2404 "{cache}"'
    )


def _hand_register(tmp_path: Path, image: bytes) -> Path:
    """A registration with no stamp (added before #1248, or by hand)."""
    provider = _provider(tmp_path)
    provider.mkdir(parents=True)
    (provider / "metadata.json").write_bytes(_META)
    (provider / "box.img").write_bytes(image)
    os.utime(provider / "box.img", (500, 500))
    return provider


def test_first_reuse_adds_then_second_reuse_skips(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    first = _bash(_reuse(cache), env)
    assert first.returncode == 0, first.stderr
    assert "registration: absent" in first.stdout
    second = _bash(_reuse(cache), env)
    assert second.returncode == 0, second.stderr
    assert "registration: current" in second.stdout
    assert _adds(tmp_path) == [f"box add --provider libvirt --force obs-ubuntu2404 {cache}"]


def test_rebaked_cache_is_readded(tmp_path):
    # A rebake rewrites the cache: a new mtime (and size) means a new identity.
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    assert _bash(_reuse(cache), env).returncode == 0
    _cache(tmp_path, b"box-v2-rebaked", mtime=2_000)
    again = _bash(_reuse(cache), env)
    assert again.returncode == 0, again.stderr
    assert "registration: stale" in again.stdout
    assert len(_adds(tmp_path)) == 2
    assert (_provider(tmp_path) / "box.img").read_bytes() == b"box-v2-rebaked"


def test_manifest_change_alone_is_readded(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    assert _bash(_reuse(cache, "h1"), env).returncode == 0
    assert "registration: stale" in _bash(_reuse(cache, "h2"), env).stdout
    assert len(_adds(tmp_path)) == 2


def test_unstamped_registration_identical_to_the_cache_is_adopted(tmp_path):
    # Registered before #1248 from this very cache: stamp it, keep its box.img (and so
    # its mtime, which the libvirt base volume is named after), add nothing.
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    provider = _hand_register(tmp_path, b"box-v1")
    result = _bash(_reuse(cache), env)
    assert result.returncode == 0, result.stderr
    assert "registration: adopted" in result.stdout
    assert _adds(tmp_path) == []
    assert (provider / "box.img").stat().st_mtime == 500
    assert "registration: current" in _bash(_reuse(cache), env).stdout


def test_unstamped_registration_that_differs_is_readded(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    _hand_register(tmp_path, b"something-else")
    result = _bash(_reuse(cache), env)
    assert result.returncode == 0, result.stderr
    assert "registration: differs from this cache" in result.stdout
    assert "registration: unstamped - adding" in result.stdout
    assert len(_adds(tmp_path)) == 1
    assert "registration: current" in _bash(_reuse(cache), env).stdout


def test_unstamped_registration_with_other_metadata_is_readded(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    provider = _hand_register(tmp_path, b"box-v1")
    (provider / "metadata.json").write_text('{"provider":"libvirt"}\n')
    assert "registration: differs" in _bash(_reuse(cache), env).stdout
    assert len(_adds(tmp_path)) == 1


def test_state_is_cheap_and_names_each_case(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    state = f'box_reg_state obs-ubuntu2404 "$(box_reg_identity "{cache}" h1)"'
    assert _bash(state, env).stdout.strip() == "absent"
    _hand_register(tmp_path, b"box-v1")
    assert _bash(state, env).stdout.strip() == "unstamped"
    assert _bash(_reuse(cache), env).returncode == 0
    assert _bash(state, env).stdout.strip() == "current"
    other = f'box_reg_state obs-ubuntu2404 "$(box_reg_identity "{cache}" h2)"'
    assert _bash(other, env).stdout.strip() == "stale"


def test_removed_box_is_readded(tmp_path):
    # `box clean` / `vagrant box remove` delete the box dir, stamp included.
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    assert _bash(_reuse(cache), env).returncode == 0
    subprocess.run(  # noqa: S603
        ["rm", "-rf", str(tmp_path / "vagrant-home" / "boxes" / "obs-ubuntu2404")],  # noqa: S607
        check=True,
    )
    assert "registration: absent" in _bash(_reuse(cache), env).stdout
    assert len(_adds(tmp_path)) == 2


def test_box_register_always_adds_and_stamps(tmp_path):
    # The BUILD / FORCE-BUILD path: always add, then a REUSE of that cache is current.
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    script = (
        f'box_register obs-ubuntu2404 "$(box_reg_identity "{cache}" h1)" '
        f'--provider libvirt --force obs-ubuntu2404 "{cache}"'
    )
    assert _bash(script, env).returncode == 0
    assert _bash(script, env).returncode == 0
    assert len(_adds(tmp_path)) == 2
    assert "registration: current" in _bash(_reuse(cache), env).stdout


def test_box_register_fails_loud_when_nothing_registered(tmp_path):
    env = _env(tmp_path, FAKE_VAGRANT_EMPTY="1")
    cache = _cache(tmp_path)
    result = _bash(_reuse(cache), env)
    assert result.returncode != 0
    assert "has no registered box.img to stamp" in result.stderr


def test_same_as_cache_is_false_when_unregistered(tmp_path):
    cache = _cache(tmp_path)
    result = _bash(f'box_reg_same_as_cache obs-ubuntu2404 "{cache}"', _env(tmp_path))
    assert result.returncode == 1


def test_slashed_name_is_escaped(tmp_path):
    env = _env(tmp_path)
    cache = _cache(tmp_path)
    script = (
        f'id="$(box_reg_identity "{cache}" -)"; '
        f'box_reuse_register rhel/9.6-x86_64 "{cache}" "$id" --force rhel/9.6-x86_64 "{cache}"; '
        f'box_reg_state rhel/9.6-x86_64 "$id"'
    )
    result = _bash(script, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("current")
    assert (tmp_path / "vagrant-home" / "boxes" / "rhel-VAGRANTSLASH-9.6-x86_64").is_dir()


def test_identity_names_path_size_mtime_and_manifest(tmp_path):
    cache = tmp_path / "x.box"
    cache.write_bytes(b"12345")
    os.utime(cache, (1_234, 1_234))
    result = _bash(f'box_reg_identity "{cache}" abc', _env(tmp_path))
    assert result.stdout.strip() == f"cache={cache} size=5 mtime=1234 manifest=abc"
