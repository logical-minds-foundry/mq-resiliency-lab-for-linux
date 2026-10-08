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
        ("a-comment-line-without-equals\nID=ubuntu\n", "apt"),  # the no-`=` line is skipped
        ("", "unknown"),
    ],
)
def test_distro_family(text, want):
    assert hf.distro_family(text) == want


# A real cloud-x86 /proc/cpuinfo shape: per-processor blocks, `flags` with a tab before
# the colon. Carries every x86-64-v3 flag plus unrelated ones.
V3_CPUINFO = (
    "processor\t: 0\n"
    "vendor_id\t: GenuineIntel\n"
    "flags\t\t: fpu sse4_2 avx2 bmi1 bmi2 fma movbe f16c abm xsave popcnt\n"
    "bugs\t\t: spectre_v1\n"
    "\n"
    "processor\t: 1\n"
    "flags\t\t: fpu\n"  # only the FIRST flags line is read
)
V2_CPUINFO = "processor\t: 0\nflags\t\t: fpu sse4_2 popcnt xsave abm\n"


def _raw(
    *, machine: str = "amd64", kvm_usable: bool = True, cpuinfo_text: str = V3_CPUINFO
) -> hf.HostFacts:
    return hf.from_raw(
        machine=machine,
        kvm_usable=kvm_usable,
        os_release_text="ID=ubuntu\n",
        vergil_marker=False,
        cpuinfo_text=cpuinfo_text,
    )


def test_from_raw_builds_facts():
    assert _raw() == hf.HostFacts(
        arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=False, x86_64_v3=True
    )


def test_cpu_flags_reads_the_first_flags_line():
    flags = hf.cpu_flags(V3_CPUINFO)
    assert {"avx2", "sse4_2", "popcnt"} <= flags
    assert "processor" not in flags


def test_cpu_flags_without_a_flags_line_is_empty():
    assert hf.cpu_flags("processor\t: 0\nno-colon-line\n") == frozenset()


def test_x86_64_v3_missing_lists_absent_flags_sorted():
    assert hf.x86_64_v3_missing(V3_CPUINFO) == ()
    assert hf.x86_64_v3_missing(V2_CPUINFO) == ("avx2", "bmi1", "bmi2", "f16c", "fma", "movbe")


def test_v3_needs_every_flag_under_kvm():
    facts = _raw(cpuinfo_text=V2_CPUINFO)
    assert facts.x86_64_v3 is False
    assert facts.x86_64_v3_missing == ("avx2", "bmi1", "bmi2", "f16c", "fma", "movbe")


def test_v3_is_absent_without_kvm_even_when_the_cpu_has_it():
    """TCG: the level the emulated guest sees was never measured, so it counts as absent."""
    facts = _raw(kvm_usable=False)
    assert facts.x86_64_v3 is False
    assert facts.x86_64_v3_missing == ()  # the host CPU lacks nothing; KVM is the gap


def test_v3_is_never_set_on_aarch64():
    facts = _raw(machine="aarch64", cpuinfo_text="flags\t: fp asimd\n")
    assert facts.x86_64_v3 is False
    assert facts.x86_64_v3_missing == ()


def test_probe_reads_present_files(tmp_path):
    osr = tmp_path / "os-release"
    osr.write_text("ID=ubuntu\n")
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    vrg = tmp_path / "vergil"
    vrg.write_text("")
    cpu = tmp_path / "cpuinfo"
    cpu.write_text(V3_CPUINFO)
    f = hf.probe(
        machine=lambda: "x86_64", kvm_path=kvm, os_release=osr, vergil_marker=vrg, cpuinfo=cpu
    )
    assert f == hf.HostFacts(
        arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=True, x86_64_v3=True
    )


def test_probe_handles_absent_files(tmp_path):
    missing = tmp_path / "nope"
    f = hf.probe(
        machine=lambda: "x86_64",
        kvm_path=missing,
        os_release=missing,
        vergil_marker=missing,
        cpuinfo=missing,
    )
    assert f == hf.HostFacts(
        arch=hf.X86_64,
        kvm=False,
        distro_family="unknown",
        in_vergil=False,
        x86_64_v3=False,
        x86_64_v3_missing=hf.X86_64_V3_FLAGS,
    )


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        ({"cpuinfo_text": V2_CPUINFO}, "this host's CPU lacks avx2, bmi1, bmi2, f16c, fma, movbe"),
        ({"machine": "aarch64"}, "this host is aarch64, not x86_64"),
        ({"kvm_usable": False}, "no usable KVM, and x86-64-v3 is not measured under TCG"),
    ],
)
def test_x86_64_v3_gap_names_the_reason(kw, want):
    assert want in hf.x86_64_v3_gap(_raw(**kw))
