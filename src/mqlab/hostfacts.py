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
# The /proc/cpuinfo flags that mark an x86_64 CPU as x86-64-v3, the microarchitecture
# level some OS majors are built for (catalog `requires: [x86-64-v3]`). The cloud x86
# host's KVM host-passthrough guests carry all eight (docs/reports/2026-10-os-axis-spike.md).
# Sorted, so messages list them stably.
X86_64_V3_FLAGS = ("abm", "avx2", "bmi1", "bmi2", "f16c", "fma", "movbe", "xsave")


class HostFactError(RuntimeError):
    """The host cannot be characterised (e.g. an unknown CPU architecture)."""


@dataclass(frozen=True)
class HostFacts:
    """What the lab needs to know about the host.

    ``x86_64_v3`` is True only on an x86_64 host with usable KVM whose CPU carries every
    X86_64_V3_FLAGS flag: the lab's KVM guests run host-passthrough, so the host CPU's
    flags are the guest's. Without KVM the guest is emulated (TCG, ``cpu_mode:
    maximum``) and the level it would see was never measured, so it counts as absent.
    ``x86_64_v3_missing`` names the flags the host CPU lacks, for messages only (empty
    when it lacks none, and off x86_64).
    """

    arch: str
    kvm: bool
    distro_family: str
    in_vergil: bool
    x86_64_v3: bool
    x86_64_v3_missing: tuple[str, ...] = ()


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


def cpu_flags(cpuinfo_text: str) -> frozenset[str]:
    """The CPU flags in /proc/cpuinfo text: its first ``flags`` line (none -> empty)."""
    for line in cpuinfo_text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "flags":
            return frozenset(value.split())
    return frozenset()


def x86_64_v3_missing(cpuinfo_text: str) -> tuple[str, ...]:
    """The X86_64_V3_FLAGS the CPU that ``cpuinfo_text`` describes lacks (sorted)."""
    flags = cpu_flags(cpuinfo_text)
    return tuple(flag for flag in X86_64_V3_FLAGS if flag not in flags)


def x86_64_v3_gap(facts: HostFacts) -> str:
    """Why ``facts`` does not count as x86-64-v3 (for a refusal or doctor line)."""
    if facts.x86_64_v3_missing:
        return f"this host's CPU lacks {', '.join(facts.x86_64_v3_missing)}"
    if facts.arch != X86_64:
        return f"this host is {facts.arch}, not x86_64"
    return (
        "this host has no usable KVM, and x86-64-v3 is not measured under TCG "
        "emulation, so it counts as absent"
    )


def from_raw(
    *,
    machine: str,
    kvm_usable: bool,
    os_release_text: str,
    vergil_marker: bool,
    cpuinfo_text: str,
) -> HostFacts:
    arch = normalize_arch(machine)
    missing = x86_64_v3_missing(cpuinfo_text) if arch == X86_64 else ()
    return HostFacts(
        arch=arch,
        kvm=kvm_usable,
        distro_family=distro_family(os_release_text),
        in_vergil=vergil_marker,
        x86_64_v3=arch == X86_64 and kvm_usable and not missing,
        x86_64_v3_missing=missing,
    )


def probe(
    *,
    machine: Callable[[], str] = _machine,
    kvm_path: Path = Path("/dev/kvm"),
    os_release: Path = Path("/etc/os-release"),
    vergil_marker: Path = Path("/etc/vergil"),
    cpuinfo: Path = Path("/proc/cpuinfo"),
) -> HostFacts:
    return from_raw(
        machine=machine(),
        kvm_usable=os.access(kvm_path, os.R_OK | os.W_OK),
        os_release_text=os_release.read_text() if os_release.exists() else "",
        vergil_marker=vergil_marker.exists(),
        cpuinfo_text=cpuinfo.read_text() if cpuinfo.exists() else "",
    )
