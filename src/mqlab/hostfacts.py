"""Probe and normalise the host — the single I/O boundary for arch-gated decisions (#276).

Every consumer takes a HostFacts value, so the host-arch matrix is testable with no
real hardware. probe() is the only function that reads the live host.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from platform import machine as _machine
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

AARCH64 = "aarch64"
X86_64 = "x86_64"

_ARCH_ALIASES = {"aarch64": AARCH64, "arm64": AARCH64, "x86_64": X86_64, "amd64": X86_64}
_APT = {"ubuntu", "debian"}
_DNF = {"rhel", "fedora", "centos", "almalinux", "rocky"}


class HostFactError(RuntimeError):
    """The host cannot be characterised (e.g. an unknown CPU architecture)."""


@dataclass(frozen=True)
class HostFacts:
    arch: str
    kvm: bool
    distro_family: str
    in_vergil: bool


def normalize_arch(machine: str) -> str:
    key = machine.strip().lower()
    if key not in _ARCH_ALIASES:
        raise HostFactError(f"unsupported host architecture: {machine!r}")
    return _ARCH_ALIASES[key]


def distro_family(os_release_text: str) -> str:
    ids: dict[str, str] = {}
    for line in os_release_text.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            ids[key.strip()] = val.strip().strip('"').lower()
    tokens = {ids.get("ID", "")} | set(ids.get("ID_LIKE", "").split())
    if _APT & tokens:
        return "apt"
    if _DNF & tokens:
        return "dnf"
    return "unknown"


def from_raw(
    *, machine: str, kvm_usable: bool, os_release_text: str, vergil_marker: bool
) -> HostFacts:
    return HostFacts(
        arch=normalize_arch(machine),
        kvm=kvm_usable,
        distro_family=distro_family(os_release_text),
        in_vergil=vergil_marker,
    )


def probe(
    *,
    machine: Callable[[], str] = _machine,
    kvm_path: Path = Path("/dev/kvm"),
    os_release: Path = Path("/etc/os-release"),
    vergil_marker: Path = Path("/etc/vergil"),
) -> HostFacts:
    return from_raw(
        machine=machine(),
        kvm_usable=os.access(kvm_path, os.R_OK | os.W_OK),
        os_release_text=os_release.read_text() if os_release.exists() else "",
        vergil_marker=vergil_marker.exists(),
    )
