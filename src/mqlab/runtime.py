"""The pinned guest Python runtime on the dev host (epic .github#294 spec §5.2).

``mqlab component build`` runs component tests on the SAME python-build-standalone
build the guests run (``runtime.python`` in lab/versions.yaml): never a uv-managed
interpreter (uv's interpreter list lags pbs releases, and a uv install leaves no tarball
whose hash could be checked against the pin), never the container's Python.

Two locations, deliberately different (spike report docs/reports/2026-10-guest-runtime-
spike.md, correction C3):

- the **tarball** is cached in ``build/cache/runtime/`` (shared, re-fetchable; a single
  file is safe on any filesystem) and re-verified against the pin on EVERY use;
- it is **unpacked** under ``$XDG_CACHE_HOME/mqlab/runtime/`` on the dev VM's own Linux
  filesystem: ``build/`` is a case-insensitive macOS virtiofs mount, and the tarball's
  ``share/terminfo`` holds case-colliding names (``2621A``/``2621a``).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from mqlab.paths import cache

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.versions import RuntimePin

_ARCH_ALIASES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
_FIX = "check runtime.python in lab/versions.yaml against the release's SHA256SUMS"


class RuntimePinError(RuntimeError):
    """The pinned runtime cannot be obtained or does not match the pin."""


def host_arch(machine: str | None = None) -> str:
    """This host's arch in pin terms (``x86_64`` / ``aarch64``)."""
    raw = machine if machine is not None else platform.machine()
    arch = _ARCH_ALIASES.get(raw.lower())
    if arch is None:
        raise RuntimePinError(f"no pinned guest runtime for host arch {raw!r} (x86_64/aarch64)")
    return arch


def tarball_dir() -> Path:
    """Where pinned tarballs are cached: build/cache/runtime (shared bucket)."""
    return cache("runtime")


def unpack_root() -> Path:
    """Where pinned tarballs are unpacked on the dev host: a Linux filesystem, NOT build/."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "mqlab" / "runtime"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_url(url: str, dest: Path) -> None:  # pragma: no cover - live network seam
    urllib.request.urlretrieve(url, dest)  # noqa: S310 - pinned https URL, hash-verified after


def ensure_tarball(
    pin: RuntimePin, arch: str, *, fetch: Callable[[str, Path], None] = fetch_url
) -> Path:
    """The pinned tarball for ``arch``, downloaded if absent, ALWAYS sha256-verified.

    A mismatch deletes the file and fails loudly naming both digests: it is never
    re-downloaded silently (a mismatched cache is a pin or supply problem to look at).
    """
    name = pin.tarball(arch)  # VersionError for an unpinned arch
    expected = pin.sha256[arch]
    path = tarball_dir() / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(name + ".partial")
        fetch(pin.url(arch), partial)
        partial.replace(path)
    actual = sha256_file(path)
    if actual != expected:
        path.unlink()
        raise RuntimePinError(
            f"{name}: sha256 {actual} does not match the pin {expected}; the file was "
            f"removed — {_FIX}"
        )
    return path


def interpreter_dir(pin: RuntimePin, arch: str) -> Path:
    """The unpacked runtime for exactly this build: keyed by the pin token and arch."""
    return unpack_root() / f"cpython-{pin.token}-{arch}"


def interpreter_path(pin: RuntimePin, arch: str) -> Path:
    """The pinned interpreter binary inside :func:`interpreter_dir`."""
    return interpreter_dir(pin, arch) / "python" / "bin" / f"python{pin.minor}"


def ensure_interpreter(
    pin: RuntimePin,
    arch: str | None = None,
    *,
    fetch: Callable[[str, Path], None] = fetch_url,
) -> Path:
    """The pinned interpreter for ``arch`` (default: this host), unpacked once.

    The tarball is verified first (:func:`ensure_tarball`). It unpacks into a sibling
    temp dir that is renamed into place only once complete, so a crash never leaves a
    half-unpacked interpreter that a later call would trust.
    """
    arch = arch if arch is not None else host_arch()
    tarball = ensure_tarball(pin, arch, fetch=fetch)
    target = interpreter_dir(pin, arch)
    binary = interpreter_path(pin, arch)
    if binary.is_file():
        return binary
    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(tarball) as tar:
        tar.extractall(staging, filter="data")
    if not (staging / "python" / "bin" / f"python{pin.minor}").is_file():
        shutil.rmtree(staging)
        raise RuntimePinError(
            f"{tarball.name} has no python/bin/python{pin.minor}: not an install_only "
            f"build — {_FIX}"
        )
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return binary
