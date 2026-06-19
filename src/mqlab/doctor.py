"""Host preflight: enforce the native-KVM requirement and diagnose missing tools (#276).

Diagnoses and suggests; never installs (D5). Pure given an injected `which`, so the
checklist matrix is testable. Inside Vergil the profile guarantees prerequisites, so
the check short-circuits. This is a host-capability check only — it is deliberately
*not* setup-aware (artifact presence is enforced by artifact.ensure_mq_tarballs, #266).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mqlab.hostfacts import AARCH64, HostFacts

if TYPE_CHECKING:
    from collections.abc import Callable

# tool name -> package name per distro family
_PACKAGES = {
    "qemu-system-x86_64": {"apt": "qemu-system-x86", "dnf": "qemu-kvm"},
    "qemu-system-aarch64": {"apt": "qemu-system-arm", "dnf": "qemu-kvm"},
    "virsh": {"apt": "libvirt-clients", "dnf": "libvirt-client"},
    "vagrant": {"apt": "vagrant", "dnf": "vagrant"},
    "ansible": {"apt": "ansible", "dnf": "ansible-core"},
    "genisoimage": {"apt": "genisoimage", "dnf": "genisoimage"},
}
_INSTALLER = {"apt": "sudo apt install -y", "dnf": "sudo dnf install -y"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


def _required_tools(facts: HostFacts) -> list[str]:
    tools = ["qemu-system-x86_64", "virsh", "vagrant", "ansible", "genisoimage"]
    if facts.arch == AARCH64:
        tools.insert(1, "qemu-system-aarch64")
    return tools


def _install_hint(tool: str, family: str) -> str | None:
    installer = _INSTALLER.get(family)
    pkg = _PACKAGES[tool].get(family)
    if installer is None or pkg is None:
        return None
    return f"{installer} {pkg}"


def run_checks(facts: HostFacts, *, which: Callable[[str], str | None]) -> list[Check]:
    if facts.in_vergil:
        return [Check("vergil", True, "Vergil-managed host; prerequisites guaranteed by the profile")]
    checks = [
        Check(
            "kvm",
            facts.kvm,
            "native /dev/kvm usable" if facts.kvm else "native KVM unavailable",
            None if facts.kvm else "enable nested virtualization / KVM for this host",
        ),
    ]
    for tool in _required_tools(facts):
        present = which(tool) is not None
        checks.append(
            Check(
                tool,
                present,
                "found" if present else "missing",
                None if present else _install_hint(tool, facts.distro_family),
            )
        )
    return checks


def summarise(checks: list[Check]) -> tuple[bool, str]:
    ok = all(c.ok for c in checks)
    lines = [
        f"[{'ok  ' if c.ok else 'FAIL'}] {c.name}: {c.detail}" + (f"  -> {c.fix}" if c.fix else "")
        for c in checks
    ]
    return ok, "\n".join(lines)
