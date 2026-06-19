from __future__ import annotations

from mqlab import doctor as d
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

VERGIL = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
X86_NOKVM = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=False)


def _all_present(name: str) -> str:
    return f"/usr/bin/{name}"


def _none_present(name: str) -> None:
    return None


def test_vergil_short_circuits():
    checks = d.run_checks(VERGIL, which=_all_present)
    assert [c.name for c in checks] == ["vergil"]
    assert checks[0].ok
    assert d.summarise(checks)[0] is True


def test_x86_all_present_passes():
    ok, _ = d.summarise(d.run_checks(X86, which=_all_present))
    assert ok is True


def test_x86_missing_kvm_hard_fails():
    checks = d.run_checks(X86_NOKVM, which=_all_present)
    kvm = next(c for c in checks if c.name == "kvm")
    assert kvm.ok is False
    assert d.summarise(checks)[0] is False


def test_missing_tool_yields_dnf_install_hint():
    checks = d.run_checks(X86, which=_none_present)
    virsh = next(c for c in checks if c.name == "virsh")
    assert virsh.ok is False
    assert virsh.fix is not None
    assert "dnf install" in virsh.fix


def test_missing_tool_yields_apt_hint_on_ubuntu():
    ubuntu = HostFacts(arch=X86_64, kvm=True, distro_family="apt", in_vergil=False)
    checks = d.run_checks(ubuntu, which=_none_present)
    virsh = next(c for c in checks if c.name == "virsh")
    assert virsh.fix is not None
    assert "apt install" in virsh.fix


def test_missing_tool_unknown_distro_has_no_hint():
    unknown = HostFacts(arch=X86_64, kvm=True, distro_family="unknown", in_vergil=False)
    checks = d.run_checks(unknown, which=_none_present)
    virsh = next(c for c in checks if c.name == "virsh")
    assert virsh.fix is None


def test_arm_host_requires_qemu_aarch64_too():
    arm = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=False)
    names = {c.name for c in d.run_checks(arm, which=_all_present)}
    assert "qemu-system-aarch64" in names
