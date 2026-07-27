from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from mqlab import sandeb

if TYPE_CHECKING:
    from pathlib import Path

_KERNEL = "6.8.0-106-generic"
_MODULES = "linux-modules-extra-6.8.0-106-generic"


@pytest.fixture
def sandir(tmp_path):
    d = tmp_path / "build" / "cache" / "san-debs"
    d.mkdir(parents=True)
    return d


def _touch_deb(cache_dir: Path, pkg: str, version: str = "1.0", arch: str = "arm64") -> Path:
    """Write a placeholder deb named the way apt would: <pkg>_<version>_<arch>.deb."""
    deb = cache_dir / f"{pkg}_{version}_{arch}.deb"
    deb.write_bytes(b"DEB")
    return deb


def test_kernel_modules_pkg():
    assert sandeb.kernel_modules_pkg(_KERNEL) == _MODULES


def test_san_deb_packages_includes_the_three_named_packages():
    assert sandeb.san_deb_packages(_KERNEL) == ["drbd-utils", "targetcli-fb", _MODULES]


def test_cached_deb_hit_and_miss(sandir):
    assert sandeb.cached_deb(sandir, "drbd-utils") is None
    deb = _touch_deb(sandir, "drbd-utils", "9.28.0-1")
    assert sandeb.cached_deb(sandir, "drbd-utils") == deb


def test_cached_deb_matches_only_the_named_package_prefix(sandir):
    # A different package sharing a leading token must not be mistaken for a hit.
    _touch_deb(sandir, "drbd-utils-dbg")
    assert sandeb.cached_deb(sandir, "drbd-utils") is None


def test_ensure_all_cached_downloads_nothing(sandir):
    for pkg in sandeb.san_deb_packages(_KERNEL):
        _touch_deb(sandir, pkg)
    calls: list[str] = []
    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=lambda p, d: calls.append(p))
    assert calls == []  # every package already cached: no apt-get download
    assert results == dict.fromkeys(sandeb.san_deb_packages(_KERNEL), "cached")


def test_ensure_cache_miss_downloads_each(sandir):
    def fake_download(pkg: str, dest: Path) -> None:
        _touch_deb(dest, pkg)

    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=fake_download)
    assert results == dict.fromkeys(sandeb.san_deb_packages(_KERNEL), "downloaded")
    assert sandeb.cached_deb(sandir, _MODULES) is not None


def test_ensure_creates_the_cache_dir_when_absent(tmp_path):
    missing = tmp_path / "build" / "cache" / "san-debs"
    assert not missing.exists()

    def fake_download(pkg: str, dest: Path) -> None:
        _touch_deb(dest, pkg)

    sandeb.ensure_san_debs(missing, _KERNEL, download=fake_download)
    assert missing.is_dir()


def test_ensure_download_failure_is_loud_but_not_fatal(sandir, capsys):
    def boom(pkg: str, dest: Path) -> None:
        raise subprocess.CalledProcessError(1, ["apt-get", "download", pkg])

    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=boom)
    assert results == dict.fromkeys(sandeb.san_deb_packages(_KERNEL), "unavailable")
    err = capsys.readouterr().err
    assert "could not pre-cache SAN deb" in err
    assert "network" in err  # the fallback is named, not hidden


def test_ensure_oserror_failure_is_handled(sandir):
    def boom(pkg: str, dest: Path) -> None:
        raise OSError("apt-get not found")

    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=boom)
    assert results["drbd-utils"] == "unavailable"


def test_ensure_download_that_writes_no_deb_is_unavailable(sandir, capsys):
    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=lambda p, d: None)
    assert results == dict.fromkeys(sandeb.san_deb_packages(_KERNEL), "unavailable")
    assert "produced no .deb" in capsys.readouterr().err


def test_ensure_mixed_hit_download_and_miss(sandir):
    # drbd-utils already cached; targetcli-fb downloads cleanly; modules-extra fails.
    _touch_deb(sandir, "drbd-utils")

    def selective(pkg: str, dest: Path) -> None:
        if pkg == "targetcli-fb":
            _touch_deb(dest, pkg)
            return
        raise subprocess.CalledProcessError(1, ["apt-get", "download", pkg])

    results = sandeb.ensure_san_debs(sandir, _KERNEL, download=selective)
    assert results == {
        "drbd-utils": "cached",
        "targetcli-fb": "downloaded",
        _MODULES: "unavailable",
    }
