from __future__ import annotations

import dataclasses
import hashlib
from typing import TYPE_CHECKING

import pytest

from mqlab import artifact
from mqlab.versions import load_catalog

if TYPE_CHECKING:
    from pathlib import Path

_NAME = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _arm64_box(role: str):
    """A catalog Ubuntu box pinned to aarch64, so the tarball arch is host-independent."""
    cat = load_catalog()
    entry = cat.box(role, cat.infra)
    return dataclasses.replace(entry, os=dataclasses.replace(entry.os, arch_pin="aarch64"))


_BOXES = {_arm64_box("mq-client")}


@pytest.fixture
def mqdir(tmp_path):
    d = tmp_path / "build" / "cache" / "mq"
    d.mkdir(parents=True)
    return d


def test_cache_hit_uses_local_copy_and_verifies_sha(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text(_sha(mqdir / _NAME) + f"  {_NAME}\n")
    calls = []
    out = artifact.ensure_mq_tarballs_for_boxes(
        _BOXES, "9.4.5.0", mqdir, fetch=lambda n, d: calls.append(n)
    )
    assert calls == []  # cache hit: no download
    assert out == [mqdir / _NAME]


def test_cache_miss_downloads_then_returns(mqdir):
    def fake_fetch(n, dest):
        dest.write_bytes(b"DOWNLOADED")

    out = artifact.ensure_mq_tarballs_for_boxes(_BOXES, "9.4.5.0", mqdir, fetch=fake_fetch)
    assert (mqdir / _NAME).read_bytes() == b"DOWNLOADED"
    assert out == [mqdir / _NAME]


def test_missing_sidecar_is_recorded_loudly_never_silently_skipped(mqdir, capsys):
    # IBM publishes no checksum beside the tarballs, so a hand-placed tarball with no
    # sidecar gets its digest RECORDED (with a NOTICE) rather than passing unverified;
    # a later acquire then verifies against it (#1407).
    (mqdir / _NAME).write_bytes(b"TARBALL")
    artifact.ensure_mq_tarballs_for_boxes(_BOXES, "9.4.5.0", mqdir, fetch=lambda n, d: None)
    sidecar = mqdir / f"{_NAME}.sha256"
    assert sidecar.read_text() == f"{_sha(mqdir / _NAME)}  {_NAME}\n"
    assert f"NOTICE: {_NAME} had no .sha256 sidecar" in capsys.readouterr().err
    # The recorded digest now guards the cache: a tampered tarball fails loud.
    (mqdir / _NAME).write_bytes(b"TAMPERED")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        artifact.ensure_mq_tarballs_for_boxes(_BOXES, "9.4.5.0", mqdir, fetch=lambda n, d: None)


def test_sha_mismatch_raises(mqdir):
    (mqdir / _NAME).write_bytes(b"TARBALL")
    (mqdir / f"{_NAME}.sha256").write_text("deadbeef  " + _NAME + "\n")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        artifact.ensure_mq_tarballs_for_boxes(_BOXES, "9.4.5.0", mqdir, fetch=lambda n, d: None)


def test_fetch_failure_propagates(mqdir):
    def boom(n, dest):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        artifact.ensure_mq_tarballs_for_boxes(_BOXES, "9.4.5.0", mqdir, fetch=boom)


def test_boxes_sharing_a_tarball_fetch_it_once(mqdir):
    calls = []

    def fake_fetch(n, dest):
        calls.append(n)
        dest.write_bytes(b"DOWNLOADED")

    boxes = {_arm64_box("mq-client"), _arm64_box("obs")}  # same family + arch
    out = artifact.ensure_mq_tarballs_for_boxes(boxes, "9.4.5.0", mqdir, fetch=fake_fetch)
    assert calls == [_NAME]
    assert out == [mqdir / _NAME]


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
