from __future__ import annotations

import pytest

from mqlab import hostfacts as hf


@pytest.mark.parametrize(
    ("raw", "want"),
    [("aarch64", hf.AARCH64), ("arm64", hf.AARCH64), ("x86_64", hf.X86_64), ("amd64", hf.X86_64)],
)
def test_normalize_arch_folds_aliases(raw, want):
    assert hf.normalize_arch(raw) == want


def test_normalize_arch_rejects_unknown():
    with pytest.raises(hf.HostFactError):
        hf.normalize_arch("riscv64")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("ID=ubuntu\nID_LIKE=debian\n", "apt"),
        ('ID="rhel"\nID_LIKE="fedora"\n', "dnf"),
        ('ID=almalinux\nID_LIKE="rhel centos fedora"\n', "dnf"),
        ("ID=arch\n", "unknown"),
        ("", "unknown"),
    ],
)
def test_distro_family(text, want):
    assert hf.distro_family(text) == want


def test_from_raw_builds_facts():
    f = hf.from_raw(machine="amd64", kvm_usable=True, os_release_text="ID=ubuntu\n", vergil_marker=False)
    assert f == hf.HostFacts(arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=False)


def test_probe_reads_present_files(tmp_path):
    osr = tmp_path / "os-release"
    osr.write_text("ID=ubuntu\n")
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    vrg = tmp_path / "vergil"
    vrg.write_text("")
    f = hf.probe(machine=lambda: "x86_64", kvm_path=kvm, os_release=osr, vergil_marker=vrg)
    assert f == hf.HostFacts(arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=True)


def test_probe_handles_absent_files(tmp_path):
    missing = tmp_path / "nope"
    f = hf.probe(machine=lambda: "aarch64", kvm_path=missing, os_release=missing, vergil_marker=missing)
    assert f == hf.HostFacts(arch=hf.AARCH64, kvm=False, distro_family="unknown", in_vergil=False)
