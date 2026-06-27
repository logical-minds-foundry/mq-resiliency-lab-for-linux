from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from mqlab import artifact

if TYPE_CHECKING:
    from pathlib import Path

_NAME = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


_PLATFORMS = {"ubuntu2404-arm64"}


@pytest.fixture
def mqdir(tmp_path):
    d = tmp_path / "build" / "cache" / "mq"
    d.mkdir(parents=True)
    return d


def test_cache_hit_uses_local_copy_and_verifies_sha(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text(_sha(mqdir / _NAME) + f"  {_NAME}\n")
    calls = []
    out = artifact.ensure_mq_tarballs_for_platforms(
        _PLATFORMS, "9.4.5.0", mqdir, fetch=lambda n, d: calls.append(n)
    )
    assert calls == []  # cache hit: no download
    assert out == [mqdir / _NAME]


def test_cache_miss_downloads_then_returns(mqdir):
    def fake_fetch(n, dest):
        dest.write_bytes(b"DOWNLOADED")

    out = artifact.ensure_mq_tarballs_for_platforms(_PLATFORMS, "9.4.5.0", mqdir, fetch=fake_fetch)
    assert (mqdir / _NAME).read_bytes() == b"DOWNLOADED"
    assert out == [mqdir / _NAME]


def test_sha_mismatch_raises(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text("deadbeef  " + _NAME + "\n")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        artifact.ensure_mq_tarballs_for_platforms(
            _PLATFORMS, "9.4.5.0", mqdir, fetch=lambda n, d: None
        )


def test_fetch_failure_propagates(mqdir):
    def boom(n, dest):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        artifact.ensure_mq_tarballs_for_platforms(_PLATFORMS, "9.4.5.0", mqdir, fetch=boom)


def test_mq_tarball_url():
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    assert artifact.mq_tarball_url(name) == f"{artifact.MQ_CDN_BASE}/{name}"


def test_download_mq_tarball_writes_dest_and_records_sidecar(tmp_path):
    seen = {}

    def fake_download(url: str, part: Path) -> None:
        seen["url"] = url
        part.write_bytes(b"TARBALL")

    dest = tmp_path / "mq" / "x.tar.gz"
    artifact.download_mq_tarball("x.tar.gz", dest, download=fake_download)
    assert dest.read_bytes() == b"TARBALL"
    assert seen["url"].endswith("/x.tar.gz")
    sidecar = dest.with_name("x.tar.gz.sha256")
    assert hashlib.sha256(b"TARBALL").hexdigest() in sidecar.read_text()


def test_download_mq_tarball_keeps_existing_sidecar(tmp_path):
    dest = tmp_path / "x.tar.gz"
    (tmp_path / "x.tar.gz.sha256").write_text("preexisting  x.tar.gz\n")

    def fake_download(url: str, part: Path) -> None:
        part.write_bytes(b"NEW")

    artifact.download_mq_tarball("x.tar.gz", dest, download=fake_download)
    assert (tmp_path / "x.tar.gz.sha256").read_text() == "preexisting  x.tar.gz\n"
