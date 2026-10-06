"""The pinned guest runtime on the dev host (epic .github#294 T4, spec §5.2, spike C3)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from typing import TYPE_CHECKING

import pytest

from mqlab import runtime
from mqlab.runtime import RuntimePinError
from mqlab.versions import RuntimePin, VersionError

if TYPE_CHECKING:
    from pathlib import Path


def _tarball(*, with_binary: bool = True) -> bytes:
    """A tiny install_only-shaped tarball: python/bin/python3.14 (+ a python3 symlink)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if with_binary:
            data = b"#!/bin/sh\necho fake\n"
            info = tarfile.TarInfo("python/bin/python3.14")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("python/bin/python3")
            link.type = tarfile.SYMTYPE
            link.linkname = "python3.14"
            tar.addfile(link)
        doc = b"terminfo"
        info = tarfile.TarInfo("python/share/terminfo/2/2621A")
        info.size = len(doc)
        tar.addfile(info, io.BytesIO(doc))
    return buf.getvalue()


def _pin(blob: bytes) -> RuntimePin:
    digest = hashlib.sha256(blob).hexdigest()
    return RuntimePin("3.14.8", "20261003", {"x86_64": digest, "aarch64": digest})


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(runtime, "cache", lambda *parts: tmp_path.joinpath("cache", *parts))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    return tmp_path


class Fetcher:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.urls: list[str] = []

    def __call__(self, url: str, dest: Path) -> None:
        self.urls.append(url)
        dest.write_bytes(self.blob)


@pytest.mark.parametrize(
    ("machine", "arch"),
    [("x86_64", "x86_64"), ("AMD64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64")],
)
def test_host_arch_normalises(machine, arch):
    assert runtime.host_arch(machine) == arch


def test_host_arch_defaults_to_this_machine(monkeypatch):
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")
    assert runtime.host_arch() == "aarch64"


def test_host_arch_unknown():
    with pytest.raises(RuntimePinError, match="host arch 's390x'"):
        runtime.host_arch("s390x")


def test_tarball_dir_is_the_shared_cache(dirs):
    assert runtime.tarball_dir() == dirs / "cache" / "runtime"


def test_unpack_root_honors_xdg(dirs):
    assert runtime.unpack_root() == dirs / "xdg" / "mqlab" / "runtime"


def test_unpack_root_defaults_to_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(runtime.Path, "home", lambda: tmp_path)
    assert runtime.unpack_root() == tmp_path / ".cache" / "mqlab" / "runtime"


def test_ensure_tarball_downloads_then_verifies(dirs):
    blob = _tarball()
    fetch = Fetcher(blob)
    path = runtime.ensure_tarball(_pin(blob), "aarch64", fetch=fetch)
    assert path.read_bytes() == blob
    assert fetch.urls == [_pin(blob).url("aarch64")]
    assert not path.with_name(path.name + ".partial").exists()


def test_ensure_tarball_reuses_and_reverifies_cached_file(dirs):
    blob = _tarball()
    fetch = Fetcher(blob)
    runtime.ensure_tarball(_pin(blob), "x86_64", fetch=fetch)
    runtime.ensure_tarball(_pin(blob), "x86_64", fetch=fetch)
    assert len(fetch.urls) == 1  # cached, but its hash was checked again


def test_ensure_tarball_mismatch_deletes_and_names_both_digests(dirs):
    blob = _tarball()
    pin = RuntimePin("3.14.8", "20261003", {"x86_64": "0" * 64, "aarch64": "0" * 64})
    with pytest.raises(RuntimePinError) as exc:
        runtime.ensure_tarball(pin, "x86_64", fetch=Fetcher(blob))
    message = str(exc.value)
    assert hashlib.sha256(blob).hexdigest() in message
    assert "0" * 64 in message
    assert "lab/versions.yaml" in message
    assert not (runtime.tarball_dir() / pin.tarball("x86_64")).exists()


def test_ensure_tarball_unpinned_arch(dirs):
    with pytest.raises(VersionError, match="no build for arch 's390x'"):
        runtime.ensure_tarball(_pin(b""), "s390x", fetch=Fetcher(b""))


def test_ensure_interpreter_unpacks_once_off_build(dirs, monkeypatch):
    blob = _tarball()
    pin = _pin(blob)
    binary = runtime.ensure_interpreter(pin, "aarch64", fetch=Fetcher(blob))
    assert (
        binary == dirs / "xdg/mqlab/runtime/cpython-3.14.8+20261003-aarch64/python/bin/python3.14"
    )
    assert binary.is_file()
    assert (binary.parent / "python3").is_symlink()
    assert "cache" not in binary.parts  # never unpacked into build/ (case-insensitive mount)

    def no_reextract(*_a, **_k):
        raise AssertionError("re-extracted an already unpacked runtime")

    monkeypatch.setattr(runtime.tarfile, "open", no_reextract)
    assert runtime.ensure_interpreter(pin, "aarch64", fetch=Fetcher(blob)) == binary


def test_ensure_interpreter_defaults_to_host_arch(dirs, monkeypatch):
    blob = _tarball()
    monkeypatch.setattr(runtime, "host_arch", lambda: "x86_64")
    binary = runtime.ensure_interpreter(_pin(blob), fetch=Fetcher(blob))
    assert "cpython-3.14.8+20261003-x86_64" in str(binary)


def test_ensure_interpreter_clears_a_stale_partial_and_target(dirs):
    blob = _tarball()
    pin = _pin(blob)
    target = runtime.interpreter_dir(pin, "aarch64")
    (target.with_name(target.name + ".partial") / "junk").mkdir(parents=True)
    (target / "leftover").mkdir(parents=True)  # a target without the binary is not trusted
    binary = runtime.ensure_interpreter(pin, "aarch64", fetch=Fetcher(blob))
    assert binary.is_file()
    assert not (target / "leftover").exists()
    assert not target.with_name(target.name + ".partial").exists()


def test_ensure_interpreter_refuses_a_tarball_without_the_binary(dirs):
    blob = _tarball(with_binary=False)
    pin = _pin(blob)
    with pytest.raises(RuntimePinError, match="no python/bin/python3.14"):
        runtime.ensure_interpreter(pin, "aarch64", fetch=Fetcher(blob))
    target = runtime.interpreter_dir(pin, "aarch64")
    assert not target.exists()
    assert not target.with_name(target.name + ".partial").exists()
