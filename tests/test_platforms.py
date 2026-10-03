from __future__ import annotations

import dataclasses

import pytest
import yaml

from mqlab import platforms as p
from mqlab import topology
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.versions import load_catalog, node_boxes

ARM_KVM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86_KVM = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
X86_NOKVM = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=False)

TOPO = {
    "defaults": {"cpus": 1, "memory": 1024},
    "groups": {"rdqm_a": ["rdqm-a1"], "san_a": ["san-a"]},
    "stacks": {"rdqm-rhel": {"os_family": "rhel", "groups": ["rdqm_a"]}},
    "nodes": {
        "obs": {"box": "obs", "cpus": 2, "memory": 4096, "nics": {"net-mgmt": "10.50.0.2"}},
        "rdqm-a1": {
            "box": "mq-rdqm",
            "extra_disk": 10,
            "nics": {"net-mgmt": "10.50.0.31"},
        },
        "san-a": {"box": "base", "nics": {"net-mgmt": "10.50.0.5"}},
    },
}
BOXES = node_boxes(TOPO, load_catalog())
CATALOG = load_catalog()
UBUNTU24 = CATALOG.oses[CATALOG.infra]
RHEL9 = next(e for r, e in CATALOG.oses.items() if r.family == "rhel")


def test_resolve_arm_host_ubuntu_is_native_kvm():
    obs = p.resolve(TOPO, ARM_KVM, BOXES)["obs"]
    assert (obs.os, obs.box, obs.arch, obs.driver, obs.cpu_mode) == (
        str(CATALOG.infra),
        f"obs-{CATALOG.infra.token}",
        AARCH64,
        "kvm",
        "host-passthrough",
    )
    assert obs.loader and obs.nvram and obs.input_bus == "virtio"
    assert obs.boot_timeout is None and obs.machine_arch is None


def test_resolve_arm_host_rhel_is_foreign_tcg():
    n = p.resolve(TOPO, ARM_KVM, BOXES)["rdqm-a1"]
    assert (n.arch, n.driver, n.cpu_mode, n.boot_timeout) == (X86_64, "qemu", "maximum", 1800)
    assert n.machine_arch == X86_64 and n.machine_type == "q35" and n.loader is None
    assert n.dvd == f"{p.LIBVIRT_POOL}/{RHEL9.iso}" and n.extra_disk == 10


def test_resolve_x86_host_everything_native_kvm():
    res = p.resolve(TOPO, X86_KVM, BOXES)
    assert res["obs"].arch == X86_64
    for n in res.values():
        assert n.arch == X86_64 and n.driver == "kvm" and n.cpu_mode == "host-passthrough"
        assert n.boot_timeout is None


def test_resolve_box_version_pins_only_the_bare_base_box():
    res = p.resolve(TOPO, X86_KVM, BOXES)
    assert (res["san-a"].box, res["san-a"].box_version) == (
        UBUNTU24.base_box,
        UBUNTU24.base_box_version,
    )
    assert res["san-a"].dvd is None
    assert res["obs"].box_version is None  # a locally-baked box carries no version
    assert res["rdqm-a1"].box_version is None


def test_resolve_x86_nokvm_is_display_safe_not_raising():
    assert p.resolve(TOPO, X86_NOKVM, BOXES)["obs"].driver == "qemu"


def test_require_native_kvm_raises_without_kvm():
    with pytest.raises(p.PlatformError):
        p.require_native_kvm(X86_NOKVM)
    p.require_native_kvm(X86_KVM)


def test_resolve_guards_arm64_on_x86():
    pinned = dataclasses.replace(BOXES["obs"].os, arch_pin=AARCH64)
    weird = dataclasses.replace(BOXES["obs"], os=pinned)
    topo = {**TOPO, "nodes": {"weird": {"box": "obs"}}}
    with pytest.raises(p.PlatformError, match="ARM on x86"):
        p.resolve(topo, X86_KVM, {"weird": weird})


def test_resolve_rejects_a_node_with_no_box_selection():
    topo = {**TOPO, "nodes": {"x": {"box": "obs"}}}
    with pytest.raises(p.PlatformError, match="node x: no box selected by the version layer"):
        p.resolve(topo, X86_KVM, BOXES)


def test_render_resolved_emits_nodes_yaml():
    text = p.render_resolved(TOPO, ARM_KVM, BOXES)
    doc = yaml.safe_load(text)
    assert doc["nodes"]["obs"]["driver"] == "kvm"
    assert doc["nodes"]["rdqm-a1"]["boot_timeout"] == 1800
    assert "platform" not in doc["nodes"]["obs"]


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
        "defaults: { cpus: 1, memory: 1024 }\nnodes:\n  n1: { box: infra }\n"
    )
    out = p.ensure_resolved(facts=X86_KVM)  # topo=None -> reads the file
    assert yaml.safe_load(out.read_text())["nodes"]["n1"]["arch"] == X86_64


def test_ensure_resolved_probes_when_facts_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(p, "probe", lambda: X86_KVM)  # facts=None -> probe()
    out = p.ensure_resolved(topo=TOPO)
    assert out.exists()


def test_resolved_topology_has_no_platform_key(monkeypatch, tmp_path):
    """The real topology renders through the version layer: `os` + the generated box
    name per node, never a `platform:` key (epic .github#280)."""
    real = topology.load()  # the committed topology, read before re-rooting build/
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    text = p.ensure_resolved(facts=X86_KVM, topo=real).read_text()
    assert "platform:" not in text
    assert f"box: mq-nativeha-{CATALOG.infra.token}" in text


def test_resolved_node_has_every_vagrantfile_field():
    from dataclasses import asdict

    node = p.resolve(TOPO, ARM_KVM, BOXES)["obs"]
    required = {
        "os",
        "box",
        "box_version",
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


def test_box_build_domain_virt_native_arm_ubuntu_is_kvm():
    # #732: an arm64 Ubuntu box on the arm64 host is NATIVE — KVM, not TCG.
    assert p.box_build_domain_virt("aarch64", ARM_KVM) == ("kvm", "host-passthrough")


def test_box_build_domain_virt_native_x86_is_kvm():
    assert p.box_build_domain_virt("x86_64", X86_KVM) == ("kvm", "host-passthrough")


def test_box_build_domain_virt_foreign_x86_on_arm_is_tcg():
    # x86 box (RHEL) on the arm64 Mac — foreign guest, must be TCG.
    assert p.box_build_domain_virt("x86_64", ARM_KVM) == ("qemu", "maximum")


def test_box_build_domain_virt_native_arch_without_kvm_is_tcg():
    # native arch but no usable /dev/kvm (e.g. x86 CI) — TCG.
    assert p.box_build_domain_virt("x86_64", X86_NOKVM) == ("qemu", "maximum")


def test_box_build_arch_rhel_is_x86_on_any_host():
    rhel = {"box": "rhel/9-x86_64", "arch": "x86_64"}
    assert p.box_build_arch(rhel, X86_KVM) == "x86_64"
    assert p.box_build_arch(rhel, ARM_KVM) == "x86_64"  # still x86 on the Mac


def test_box_build_arch_unpinned_ubuntu_tracks_host():
    ubuntu = {"box": "cloud-image/ubuntu-24.04"}  # no arch pin
    assert p.box_build_arch(ubuntu, X86_KVM) == "x86_64"
    assert p.box_build_arch(ubuntu, ARM_KVM) == "aarch64"


def test_is_foreign_box_build_true_for_rhel_on_arm():
    rhel = {"box": "rhel/9-x86_64", "arch": "x86_64"}
    assert p.is_foreign_box_build(rhel, ARM_KVM) is True
    assert p.is_foreign_box_build(rhel, X86_KVM) is False


def test_is_foreign_box_build_false_for_unpinned_ubuntu():
    ubuntu = {"box": "cloud-image/ubuntu-24.04"}  # host-resolved — matches any host
    assert p.is_foreign_box_build(ubuntu, ARM_KVM) is False
    assert p.is_foreign_box_build(ubuntu, X86_KVM) is False


def test_resolve_unpinned_ubuntu_fat_box_tracks_host():
    # #103 D3/D10: an un-pinned Ubuntu fat box (the catalog's Ubuntu entry has no arch
    # pin) resolves its guest arch from the host — native arm64 on Apple Silicon, x86_64
    # on the cloud — via the box_build_arch authority. RHEL keeps its catalog pin.
    topo = {
        "defaults": {"cpus": 1, "memory": 1024},
        "nodes": {"svc": {"box": "mq-client", "nics": {"net-mgmt": "10.50.0.50"}}},
    }
    boxes = node_boxes(topo, CATALOG)
    arm = p.resolve(topo, ARM_KVM, boxes)["svc"]
    assert (arm.arch, arm.driver) == (AARCH64, "kvm")  # native arm64 on the Mac
    assert p.resolve(topo, X86_KVM, boxes)["svc"].arch == X86_64  # tracks x86 on the cloud
