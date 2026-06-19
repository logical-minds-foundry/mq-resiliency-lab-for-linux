from __future__ import annotations

import hashlib

import pytest

from mqlab import artifact

_NAME = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def mqdir(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "setup_platforms", lambda s, facts=None: {"ubuntu2404-arm64"})
    d = tmp_path / "build" / "mq"
    d.mkdir(parents=True)
    return d


def test_cache_hit_uses_local_copy_and_verifies_sha(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text(_sha(mqdir / _NAME) + f"  {_NAME}\n")
    calls = []
    out = artifact.ensure_mq_tarballs("s", "9.4.5.0", mqdir, fetch=lambda n, d: calls.append(n))
    assert calls == []  # cache hit: no download
    assert out == [mqdir / _NAME]


def test_cache_miss_downloads_then_returns(mqdir):
    def fake_fetch(n, dest):
        dest.write_bytes(b"DOWNLOADED")

    out = artifact.ensure_mq_tarballs("s", "9.4.5.0", mqdir, fetch=fake_fetch)
    assert (mqdir / _NAME).read_bytes() == b"DOWNLOADED"
    assert out == [mqdir / _NAME]


def test_sha_mismatch_raises(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text("deadbeef  " + _NAME + "\n")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        artifact.ensure_mq_tarballs("s", "9.4.5.0", mqdir, fetch=lambda n, d: None)


def test_fetch_failure_propagates(mqdir):
    def boom(n, dest):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        artifact.ensure_mq_tarballs("s", "9.4.5.0", mqdir, fetch=boom)
