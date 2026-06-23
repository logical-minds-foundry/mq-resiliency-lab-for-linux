from __future__ import annotations

import pytest
import yaml

from mqlab import platforms as p
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

ARM_KVM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86_KVM = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
X86_NOKVM = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=False)

TOPO = {
    "boxes": {
        "ubuntu2404-arm64": {"box": "cloud-image/ubuntu-24.04", "arch": "aarch64"},
        "ubuntu2404-x86_64": {"box": "cloud-image/ubuntu-24.04", "arch": "x86_64"},
        "rhel96-x86_64": {"box": "rhel/9.6-x86_64", "arch": "x86_64", "dvd": "/iso/rhel.iso"},
    },
    "defaults": {"cpus": 1, "memory": 1024},
    "nodes": {
        "obs": {"cpus": 2, "memory": 4096, "nics": {"net-mgmt": "10.50.0.2"}},
        "rdqm-a1": {
            "platform": "rhel96-x86_64",
            "extra_disk": 10,
            "nics": {"net-mgmt": "10.50.0.31"},
        },
    },
}


def test_default_platform_tracks_host():
    assert p.default_platform(ARM_KVM) == "ubuntu2404-arm64"
    assert p.default_platform(X86_KVM) == "ubuntu2404-x86_64"


def test_resolve_arm_host_ubuntu_is_native_kvm():
    obs = p.resolve(TOPO, ARM_KVM)["obs"]
    assert (obs.platform, obs.arch, obs.driver, obs.cpu_mode) == (
        "ubuntu2404-arm64",
        AARCH64,
        "kvm",
        "host-passthrough",
    )
    assert obs.loader and obs.nvram and obs.input_bus == "virtio"
    assert obs.boot_timeout is None and obs.machine_arch is None


def test_resolve_arm_host_rhel_is_foreign_tcg():
    n = p.resolve(TOPO, ARM_KVM)["rdqm-a1"]
    assert (n.arch, n.driver, n.cpu_mode, n.boot_timeout) == (X86_64, "qemu", "maximum", 1800)
    assert n.machine_arch == X86_64 and n.machine_type == "q35" and n.loader is None
    assert n.dvd == "/iso/rhel.iso" and n.extra_disk == 10


def test_resolve_x86_host_everything_native_kvm():
    res = p.resolve(TOPO, X86_KVM)
    assert res["obs"].platform == "ubuntu2404-x86_64"
    for n in res.values():
        assert n.arch == X86_64 and n.driver == "kvm" and n.cpu_mode == "host-passthrough"
        assert n.boot_timeout is None


def test_resolve_x86_nokvm_is_display_safe_not_raising():
    assert p.resolve(TOPO, X86_NOKVM)["obs"].driver == "qemu"


def test_require_native_kvm_raises_without_kvm():
    with pytest.raises(p.PlatformError):
        p.require_native_kvm(X86_NOKVM)
    p.require_native_kvm(X86_KVM)


def test_resolve_guards_arm64_on_x86():
    topo = {**TOPO, "nodes": {"weird": {"platform": "ubuntu2404-arm64"}}}
    with pytest.raises(p.PlatformError, match="ARM on x86"):
        p.resolve(topo, X86_KVM)


def test_resolve_rejects_unknown_platform():
    topo = {**TOPO, "nodes": {"x": {"platform": "nope"}}}
    with pytest.raises(p.PlatformError, match="unknown platform"):
        p.resolve(topo, X86_KVM)


def test_render_resolved_emits_nodes_yaml():
    text = p.render_resolved(TOPO, ARM_KVM)
    doc = yaml.safe_load(text)
    assert doc["nodes"]["obs"]["driver"] == "kvm"
    assert doc["nodes"]["rdqm-a1"]["boot_timeout"] == 1800


def test_ensure_resolved_writes_file_and_requires_kvm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    out = p.ensure_resolved(facts=X86_KVM, topo=TOPO)
    assert out.exists()
    assert yaml.safe_load(out.read_text())["nodes"]["obs"]["arch"] == X86_64
    with pytest.raises(p.PlatformError):
        p.ensure_resolved(facts=X86_NOKVM, topo=TOPO)


def test_ensure_resolved_reads_real_topology_when_topo_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "boxes:\n  ubuntu2404-x86_64: { box: cloud-image/ubuntu-24.04, arch: x86_64 }\n"
        "defaults: { cpus: 1, memory: 1024 }\nnodes:\n  n1: {}\n"
    )
    out = p.ensure_resolved(facts=X86_KVM)  # topo=None -> reads the file
    assert yaml.safe_load(out.read_text())["nodes"]["n1"]["arch"] == X86_64


def test_ensure_resolved_probes_when_facts_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(p, "probe", lambda: X86_KVM)  # facts=None -> probe()
    out = p.ensure_resolved(topo=TOPO)
    assert out.exists()


def test_resolved_node_has_every_vagrantfile_field():
    from dataclasses import asdict

    node = p.resolve(TOPO, ARM_KVM)["obs"]
    required = {
        "platform",
        "box",
        "driver",
        "cpu_mode",
        "machine_arch",
        "machine_type",
        "loader",
        "nvram",
        "input_bus",
        "boot_timeout",
        "cpus",
        "memory",
        "extra_disk",
        "dvd",
        "nics",
    }
    assert required <= set(asdict(node))


def test_build_domain_virt_x86_host_with_kvm_is_native():
    assert p.build_domain_virt(X86_KVM) == ("kvm", "host-passthrough")


def test_build_domain_virt_x86_host_without_kvm_falls_back_to_tcg():
    # Defensive: the gated bring-up never reaches here (require_native_kvm), but
    # the pure function stays honest and display-safe.
    assert p.build_domain_virt(X86_NOKVM) == ("qemu", "maximum")


def test_build_domain_virt_arm_host_is_foreign_tcg():
    # x86_64 guest on an arm64 Mac is foreign-arch — must be TCG.
    assert p.build_domain_virt(ARM_KVM) == ("qemu", "maximum")
