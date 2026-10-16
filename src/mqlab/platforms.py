"""Resolve each topology node to a concrete libvirt provider config, given host facts (#276).

The single authority for the host-arch-gated virtualization matrix (design §4.2).
Pure and display-safe: callers pass HostFacts, resolve() never raises on missing KVM
(so status works); the hard native-KVM gate is require_native_kvm(), called by the
bring-up path. resolve() raises only on the structurally-impossible arm64-on-x86 (D4)
or a node with no box selection.

Which box each node boots is NOT decided here: the version layer (mqlab.versions
node_boxes, epic .github#280) hands resolve() a per-node BoxEntry selection, and this
module keeps sole ownership of the provider mechanics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

import yaml

from mqlab import topology
from mqlab.hostfacts import AARCH64, X86_64, HostFacts, probe
from mqlab.paths import resolved_topology_path
from mqlab.versions import load_catalog, node_boxes, node_role

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.versions import BoxEntry

# Provider literals — one source of truth for the matrix (design §4.2).
AAVMF_LOADER = "/usr/share/AAVMF/AAVMF_CODE.fd"
MACHINE_TYPE_X86 = "q35"
CPU_KVM = "host-passthrough"
CPU_TCG = "maximum"
TCG_BOOT_TIMEOUT = 1800
# libvirt's default image pool: a RHEL node attaches its install DVD (the catalog's
# os.<family>.<major>.iso) from here, where lab/scripts/stage-rhel-iso.sh stages it.
LIBVIRT_POOL = "/var/lib/libvirt/images"


class PlatformError(RuntimeError):
    """A node cannot be resolved to a runnable provider config."""


@dataclass(frozen=True)
class ResolvedNode:
    os: str  # the node's OS, e.g. "ubuntu:24" (from the version layer)
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
    # Guest RAM backing (#1241): "hugepages" -> the Vagrantfile adds vagrant-libvirt's
    # memorybacking :hugepages; None -> default 4 KiB backing. Carried per node (from the
    # effective topology's memory_backing lever) so the Vagrantfile stays a dumb consumer.
    memory_backing: str | None = None


def require_native_kvm(facts: HostFacts) -> None:
    """Hard native-KVM requirement (D3). The native arch always equals the host arch."""
    if not facts.kvm:
        raise PlatformError(
            "native KVM required: /dev/kvm is unavailable for this host. Enable nested "
            "virtualization / KVM. (mqlab does not run the native architecture under TCG.)"
        )


def resolve(
    topo: dict[str, Any], facts: HostFacts, boxes: dict[str, BoxEntry]
) -> dict[str, ResolvedNode]:
    """Each topology node -> its provider config, booting the box ``boxes`` selects."""
    defaults = topo.get("defaults", {})
    backing = topology.memory_backing(topo)  # validated; never read from MQLAB_ENV here
    out: dict[str, ResolvedNode] = {}
    for name, raw in topo.get("nodes", {}).items():
        if name not in boxes:
            raise PlatformError(f"node {name}: no box selected by the version layer")
        node = _provider(name, raw or {}, defaults, boxes[name], facts)
        out[name] = replace(node, memory_backing=backing)
    return out


def _provider(
    name: str,
    spec: dict[str, Any],
    defaults: dict[str, Any],
    entry: BoxEntry,
    facts: HostFacts,
) -> ResolvedNode:
    guest = box_build_arch({"arch": entry.os.arch_pin}, facts)
    if guest == AARCH64 and facts.arch == X86_64:
        raise PlatformError(f"node {name}: emulating ARM on x86 is unsupported")
    kvm = guest == facts.arch and facts.kvm
    is_arm = guest == AARCH64
    cpus, memory = node_sizing(name, spec, defaults)
    return ResolvedNode(
        os=str(entry.os.ref),
        box=entry.name,
        arch=guest,
        driver="kvm" if kvm else "qemu",
        machine_arch=None if is_arm else X86_64,
        machine_type=None if is_arm else MACHINE_TYPE_X86,
        loader=AAVMF_LOADER if is_arm else None,
        nvram=f"/var/lib/libvirt/qemu/nvram/lab-{name}_VARS.fd" if is_arm else None,
        input_bus="virtio" if is_arm else None,
        cpu_mode=CPU_KVM if kvm else CPU_TCG,
        boot_timeout=None if kvm else TCG_BOOT_TIMEOUT,
        cpus=cpus,
        memory=memory,
        extra_disk=spec.get("extra_disk"),
        dvd=f"{LIBVIRT_POOL}/{entry.os.iso}" if entry.os.iso else None,
        nics=spec.get("nics", {}),
    )


def node_sizing(name: str, spec: dict[str, Any], defaults: dict[str, Any]) -> tuple[int, int]:
    """(cpus, memory MiB) for one topology node: its own values, else the topology
    ``defaults``, else 1 vCPU / 1024 MiB. Both must be positive integers; anything else
    fails loud, so a typo never reaches the Vagrantfile or a bake domain."""
    cpus = spec.get("cpus", defaults.get("cpus", 1))
    memory = spec.get("memory", defaults.get("memory", 1024))
    for key, value in (("cpus", cpus), ("memory", memory)):
        # bool is an int subclass; `cpus: true` is a typo, not one vCPU.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PlatformError(
                f"node {name}: {key} must be a positive integer, got {value!r} — "
                "fix lab/topology.yaml"
            )
    return cpus, memory


# Bake-guest sizing (#1596). build-fatbox.sh's transient build domain used to be a fixed
# 2 vCPU / 2048 MiB for every box, while the obs node that boots the obs box runs at
# 12 vCPU / 10240 MiB: the obs bake starts OpenSearch / Dashboards / Data Prepper JVMs
# through its handlers, and a 2 GB, swapless guest ran out of memory (145 MB free) and
# dropped the bake's SSH session. A box is now baked at the size of the largest node
# that boots it (bake_size), never below the old floor.
BAKE_FLOOR_CPUS = 2
BAKE_FLOOR_MEMORY_MIB = 2048
# Ceiling for a bake guest. A bake may run while lab guests are up (commons + a stack,
# about 40 GB on a 3+3 Native HA lab), and the hosts are the cloud dev VM (16 vCPU /
# 62 GB) and the Mac Lima VM (24 vCPU / 64 GB). 12 vCPU / 12 GiB fits beside a running
# lab on both and covers the largest node today (obs: 12 / 10240). A topology node
# larger than this fails loud in bake_size rather than baking under-sized or
# over-committing the host: raising it is a deliberate host-capacity decision.
BAKE_CAP_CPUS = 12
BAKE_CAP_MEMORY_MIB = 12288


@dataclass(frozen=True)
class BakeSize:
    """The transient bake domain's size for one box role, and the nodes it came from."""

    cpus: int
    memory: int  # MiB
    nodes: tuple[str, ...]


def bake_size(topo: dict[str, Any], role: str) -> BakeSize:
    """The bake-guest size for the box ``role``: the max cpus and the max memory across
    the topology nodes that boot it (``box: <role>``), floored at BAKE_FLOOR_* (#1596).

    ``topo`` is the EFFECTIVE topology (topology.load(): the base plus the MQLAB_ENV
    profile), so a profile's per-node override sizes the bake the same way it sizes the
    node. Per-node sizing goes through node_sizing, the same authority resolve() uses.
    Fails loud when no node boots the role (there is no runtime size to bake for) or when
    the derived size exceeds BAKE_CAP_* (a host-capacity decision, never a silent clamp).
    """
    defaults = topo.get("defaults") or {}
    nodes: dict[str, Any] = topo.get("nodes") or {}
    sizes = {
        name: node_sizing(name, spec or {}, defaults)
        for name, spec in nodes.items()
        if node_role(name, spec) == role
    }
    if not sizes:
        raise PlatformError(
            f"box role {role!r}: no topology node boots it (`box: {role}`), so there is no "
            "node size to bake it at — fix lab/topology.yaml"
        )
    cpus = max(BAKE_FLOOR_CPUS, *(c for c, _ in sizes.values()))
    memory = max(BAKE_FLOOR_MEMORY_MIB, *(m for _, m in sizes.values()))
    if cpus > BAKE_CAP_CPUS or memory > BAKE_CAP_MEMORY_MIB:
        raise PlatformError(
            f"box role {role!r}: its nodes need a {cpus} vCPU / {memory} MiB bake guest, over "
            f"the {BAKE_CAP_CPUS} vCPU / {BAKE_CAP_MEMORY_MIB} MiB cap (platforms.BAKE_CAP_*) "
            "— raise the cap only after checking the hosts can give it beside a running lab"
        )
    return BakeSize(cpus=cpus, memory=memory, nodes=tuple(sorted(sizes)))


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


def render_resolved(topo: dict[str, Any], facts: HostFacts, boxes: dict[str, BoxEntry]) -> str:
    nodes = {name: asdict(node) for name, node in resolve(topo, facts, boxes).items()}
    return yaml.safe_dump({"nodes": nodes}, sort_keys=True)


def ensure_resolved(*, facts: HostFacts | None = None, topo: dict[str, Any] | None = None) -> Path:
    """Render build/work/lab/topology.resolved.yaml. Enforces the native-KVM gate (D3).

    The per-node box selection comes from the version layer (versions.node_boxes over
    lab/versions.yaml and the per-stack instance records)."""
    facts = facts if facts is not None else probe()
    require_native_kvm(facts)
    if topo is None:
        topo = topology.load()  # effective topology: base + MQLAB_ENV profile (#1202)
    boxes = node_boxes(topo, load_catalog())
    path = resolved_topology_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_resolved(topo, facts, boxes))
    return path
