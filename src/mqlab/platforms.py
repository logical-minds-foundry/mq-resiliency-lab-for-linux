"""Resolve each topology node to a concrete libvirt provider config, given host facts (#276).

The single authority for the host-arch-gated virtualization matrix (design §4.2).
Pure and display-safe: callers pass HostFacts, resolve() never raises on missing KVM
(so status works); the hard native-KVM gate is require_native_kvm(), called by the
bring-up path. resolve() raises only on the structurally-impossible arm64-on-x86 (D4)
or an undefined platform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.hostfacts import AARCH64, X86_64, HostFacts, probe
from mqlab.paths import repo_root, resolved_topology_path

if TYPE_CHECKING:
    from pathlib import Path

# Provider literals — one source of truth for the matrix (design §4.2).
AAVMF_LOADER = "/usr/share/AAVMF/AAVMF_CODE.fd"
MACHINE_TYPE_X86 = "q35"
CPU_KVM = "host-passthrough"
CPU_TCG = "maximum"
TCG_BOOT_TIMEOUT = 1800


class PlatformError(RuntimeError):
    """A node cannot be resolved to a runnable provider config."""


@dataclass(frozen=True)
class ResolvedNode:
    platform: str
    box: str
    arch: str
    driver: str
    machine_arch: str | None
    machine_type: str | None
    loader: str | None
    nvram: str | None
    input_bus: str | None
    cpu_mode: str
    boot_timeout: int | None
    cpus: int
    memory: int
    extra_disk: int | None
    dvd: str | None
    nics: dict[str, str]


def default_platform(facts: HostFacts) -> str:
    """Native-preferred Ubuntu platform for this host (D1/D6)."""
    return "ubuntu2404-x86_64" if facts.arch == X86_64 else "ubuntu2404-arm64"


def require_native_kvm(facts: HostFacts) -> None:
    """Hard native-KVM requirement (D3). The native arch always equals the host arch."""
    if not facts.kvm:
        raise PlatformError(
            "native KVM required: /dev/kvm is unavailable for this host. Enable nested "
            "virtualization / KVM. (mqlab does not run the native architecture under TCG.)"
        )


def resolve(topo: dict[str, Any], facts: HostFacts) -> dict[str, ResolvedNode]:
    boxes = topo["boxes"]
    defaults = topo.get("defaults", {})
    out: dict[str, ResolvedNode] = {}
    for name, raw in topo.get("nodes", {}).items():
        spec = raw or {}
        platform = spec.get("platform", default_platform(facts))
        if platform not in boxes:
            raise PlatformError(f"node {name}: unknown platform {platform!r}")
        out[name] = _provider(name, spec, defaults, platform, boxes[platform], facts)
    return out


def _provider(
    name: str,
    spec: dict[str, Any],
    defaults: dict[str, Any],
    platform: str,
    box: dict[str, Any],
    facts: HostFacts,
) -> ResolvedNode:
    guest = box_build_arch(box, facts)
    if guest == AARCH64 and facts.arch == X86_64:
        raise PlatformError(f"node {name}: emulating ARM on x86 is unsupported")
    kvm = guest == facts.arch and facts.kvm
    is_arm = guest == AARCH64
    return ResolvedNode(
        platform=platform,
        box=box["box"],
        arch=guest,
        driver="kvm" if kvm else "qemu",
        machine_arch=None if is_arm else X86_64,
        machine_type=None if is_arm else MACHINE_TYPE_X86,
        loader=AAVMF_LOADER if is_arm else None,
        nvram=f"/var/lib/libvirt/qemu/nvram/lab-{name}_VARS.fd" if is_arm else None,
        input_bus="virtio" if is_arm else None,
        cpu_mode=CPU_KVM if kvm else CPU_TCG,
        boot_timeout=None if kvm else TCG_BOOT_TIMEOUT,
        cpus=spec.get("cpus", defaults.get("cpus", 1)),
        memory=spec.get("memory", defaults.get("memory", 1024)),
        extra_disk=spec.get("extra_disk"),
        dvd=box.get("dvd"),
        nics=spec.get("nics", {}),
    )


def box_build_domain_virt(box_arch: str, facts: HostFacts) -> tuple[str, str]:
    """(domain_type, cpu_mode) for a box build of the given guest arch (#103/#732).

    KVM when the box's build arch is native to the host and /dev/kvm is usable;
    TCG for a foreign-arch guest (e.g. the x86 RHEL box on an arm64 Mac) or when
    KVM is absent. This is the guest-arch-aware authority the fat-box builder
    consumes — an arm64 Ubuntu box on Apple Silicon builds under native KVM, not
    TCG. Pure and display-safe — never raises — mirroring resolve().
    """
    kvm = box_arch == facts.arch and facts.kvm
    return ("kvm", CPU_KVM) if kvm else ("qemu", CPU_TCG)


def build_domain_virt(facts: HostFacts) -> tuple[str, str]:
    """(domain_type, cpu_mode) for the local x86_64 base-OS RHEL box build.

    The base box (build-box.sh) is always an x86_64 guest, so this is the
    ``box_arch == X86_64`` case of box_build_domain_virt: KVM on a native-x86 host,
    TCG on the arm64 Mac (foreign-arch) or without usable /dev/kvm (#327).
    """
    return box_build_domain_virt(X86_64, facts)


def box_build_arch(entry: dict[str, Any], facts: HostFacts) -> str:
    """The build/guest arch for a fat/base box (design D1, #103).

    A box that pins its arch (RHEL: ``arch: x86_64``) keeps it on every host; an
    un-pinned box (host-resolved Ubuntu fat box) tracks the host. Pure and
    display-safe — never raises — mirroring resolve(). build-fatbox.sh consumes
    the result via --arch; it does not re-derive it.
    """
    return entry.get("arch") or facts.arch


def is_foreign_box_build(entry: dict[str, Any], facts: HostFacts) -> bool:
    """True when this box pins an arch other than the host's — a build that would
    be fully emulated (e.g. the RHEL box on Apple Silicon). The orchestrator
    refuses it (design D11, #103); this predicate stays pure so status paths can
    call it safely.
    """
    pinned = entry.get("arch")
    return bool(pinned) and pinned != facts.arch


def render_resolved(topo: dict[str, Any], facts: HostFacts) -> str:
    nodes = {name: asdict(node) for name, node in resolve(topo, facts).items()}
    return yaml.safe_dump({"nodes": nodes}, sort_keys=True)


def ensure_resolved(*, facts: HostFacts | None = None, topo: dict[str, Any] | None = None) -> Path:
    """Render build/work/lab/topology.resolved.yaml. Enforces the native-KVM gate (D3)."""
    facts = facts if facts is not None else probe()
    require_native_kvm(facts)
    if topo is None:
        topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    path = resolved_topology_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_resolved(topo, facts))
    return path
