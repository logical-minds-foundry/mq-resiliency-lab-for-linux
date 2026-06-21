from __future__ import annotations

import pytest

from mqlab import manifest as m


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "manifests_root", lambda: tmp_path / "manifests")
    _write(
        tmp_path,
        "manifests/distributed-pcmk-ubuntu/default.yaml",
        "mq:\n  version: '9.4.5.0'\n"
        "os:\n  box: cloud-image/ubuntu-24.04\n  box_version: '20260518.0.0'\n",
    )
    _write(
        tmp_path,
        "manifests/_shared/observability.yaml",
        "prometheus: '2.53.2'\nnode_exporter: '1.8.2'\nloki: '3.1.0'\n"
        "alloy: '1.3.0'\ngrafana: '11.1.0'\nmq_metric_samples_ref: 'v5.6.4'\n",
    )
    return tmp_path


def test_load_manifest_merges_sut_and_shared_obs(manifests):
    man = m.load_manifest("distributed-pcmk-ubuntu")
    assert man.mq_version == "9.4.5.0"
    assert man.box == "cloud-image/ubuntu-24.04"
    assert man.box_version == "20260518.0.0"
    assert man.observability["prometheus"] == "2.53.2"
    assert man.observability["mq_metric_samples_ref"] == "v5.6.4"


def test_load_manifest_missing_required_key_raises(manifests, tmp_path):
    (tmp_path / "manifests/distributed-pcmk-ubuntu/default.yaml").write_text(
        "mq: {}\nos:\n  box: x\n  box_version: y\n"
    )
    with pytest.raises(ValueError, match="missing required key mq.version"):
        m.load_manifest("distributed-pcmk-ubuntu")


def test_load_manifest_missing_file_raises(manifests):
    with pytest.raises(FileNotFoundError, match="nope/default.yaml"):
        m.load_manifest("nope")


def test_load_manifest_non_mapping_raises(manifests, tmp_path):
    (tmp_path / "manifests/distributed-pcmk-ubuntu/default.yaml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="is not a mapping"):
        m.load_manifest("distributed-pcmk-ubuntu")


def test_tarball_name_maps_version_and_arch():
    assert (
        m.tarball_name("9.4.5.0", "ubuntu2404-arm64")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    )
    assert (
        m.tarball_name("9.4.5.0", "ubuntu2404-x86_64")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
    )
    assert (
        m.tarball_name("9.4.5.0", "rhel96-x86_64")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_unknown_platform_raises():
    with pytest.raises(ValueError, match="no MQ tarball arch mapping"):
        m.tarball_name("9.4.5.0", "solaris-sparc")


def test_vars_overlay_maps_to_role_var_names(manifests):
    man = m.load_manifest("distributed-pcmk-ubuntu")
    ov = m.vars_overlay(man)
    assert ov["mq_version"] == "9.4.5.0"
    assert ov["lab_box_version"] == "20260518.0.0"
    assert ov["prometheus_version"] == "2.53.2"
    assert ov["mq_exporter_ref"] == "v5.6.4"


def test_obs_overlay_reads_shared_manifest(manifests):
    ov = m.obs_overlay()
    assert ov["prometheus_version"] == "2.53.2"
    assert ov["grafana_version"] == "11.1.0"
    assert ov["mq_exporter_ref"] == "v5.6.4"
    assert "mq_version" not in ov  # obs-only


def test_box_version_pins_keys_by_platform(manifests, monkeypatch):
    monkeypatch.setattr(
        m,
        "_topology",
        lambda: {
            "boxes": {
                "ubuntu2404-arm64": {"box": "cloud-image/ubuntu-24.04"},
                "rhel96-x86_64": {"box": "rhel/9.6-x86_64"},
            }
        },
    )
    man = m.load_manifest("distributed-pcmk-ubuntu")  # box cloud-image/ubuntu-24.04
    assert m.box_version_pins(man) == {"ubuntu2404-arm64": "20260518.0.0"}


def test_setup_platforms_reads_topology(manifests, monkeypatch):
    monkeypatch.setattr(
        m,
        "_topology",
        lambda: {
            "setups": {"s": {"groups": ["g1", "g2"]}},
            "groups": {"g1": ["n1"], "g2": ["n2"]},
        },
    )
    monkeypatch.setattr(
        m, "lab_guests", lambda facts=None: {"n1": "ubuntu2404-arm64", "n2": "rhel96-x86_64"}
    )
    assert m.setup_platforms("s") == {"ubuntu2404-arm64", "rhel96-x86_64"}


def test_setup_platforms_threads_facts(monkeypatch):
    from mqlab.hostfacts import X86_64, HostFacts

    x86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
    seen: dict[str, object] = {}

    def fake_lab_guests(facts=None):
        seen["facts"] = facts
        return {"n1": "ubuntu2404-x86_64"}

    monkeypatch.setattr(m, "lab_guests", fake_lab_guests)
    monkeypatch.setattr(
        m, "_topology", lambda: {"groups": {"g": ["n1"]}, "setups": {"s": {"groups": ["g"]}}}
    )
    assert m.setup_platforms("s", x86) == {"ubuntu2404-x86_64"}
    assert seen["facts"] is x86


def test_topology_reads_the_real_file():
    topo = m._topology()
    assert "setups" in topo
    assert "nodes" in topo


def test_real_path_helpers():
    from mqlab.paths import manifests_root, selection_state_path

    assert manifests_root().name == "manifests"
    assert selection_state_path("foo").parts[-2:] == ("manifests", "foo.yaml")


@pytest.mark.parametrize("setup", ["distributed-pcmk-ubuntu", "distributed-rdqm-rhel"])
def test_committed_default_manifests_load(setup):
    man = m.load_manifest(setup)  # real manifests/ tree, not the fixture
    assert man.mq_version
    assert man.box and man.box_version
    assert "prometheus" in man.observability


def test_manifest_exists(manifests):
    assert m.manifest_exists("distributed-pcmk-ubuntu") is True
    assert m.manifest_exists("nope") is False


def test_resolve_selection_pins_then_reads_back(manifests, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "selection_state_path", lambda s: tmp_path / "state" / f"{s}.yaml")
    assert m.resolve_selection("distributed-pcmk-ubuntu", None) == "default"  # nothing pinned yet
    assert m.resolve_selection("distributed-pcmk-ubuntu", "default") == "default"  # pin it
    assert m.read_selection("distributed-pcmk-ubuntu") == "default"
    assert m.resolve_selection("distributed-pcmk-ubuntu", None) == "default"  # reads the pin


def test_resolve_selection_conflict_raises(manifests, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "selection_state_path", lambda s: tmp_path / "state" / f"{s}.yaml")
    m.resolve_selection("distributed-pcmk-ubuntu", "repro-945")
    with pytest.raises(ValueError, match="created against 'repro-945'"):
        m.resolve_selection("distributed-pcmk-ubuntu", "default")
