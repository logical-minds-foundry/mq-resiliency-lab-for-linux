from __future__ import annotations

import dataclasses

import pytest

from mqlab import manifest as m
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.versions import OsRef, load_catalog

ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True, x86_64_v3=False)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False, x86_64_v3=True)


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
        "manifests/_shared/observability.yaml",
        "prometheus: '2.53.2'\nnode_exporter: '1.8.2'\nloki: '3.1.0'\n"
        "alloy: '1.3.0'\ngrafana: '11.1.0'\nmq_metric_samples_ref: 'v5.6.4'\n"
        "opensearch: '3.8.0'\nopensearch_dashboards: '3.8.0'\ndata_prepper: '2.16.0'\n",
    )
    return tmp_path


_CAT = load_catalog()
_UBUNTU = _CAT.default_os("nativeha-ubuntu")
_RHEL = _CAT.default_os("rdqm-rhel")


def test_tarball_name_from_family():
    """The OS-family segment comes from the box's catalog OS entry (epic .github#280)."""
    e = _CAT.box("mq-rdqm", _RHEL)
    assert m.tarball_name("10.0.0.0", e, X86).endswith("-LinuxX64.tar.gz")


def test_tarball_name_maps_version_and_arch():
    # RHEL boxes are x86-pinned by the catalog: facts-independent — the pin decides.
    for role in ("mq-rdqm", "mq-nativeha"):
        assert (
            m.tarball_name("9.4.5.0", _CAT.box(role, _RHEL), facts=X86)
            == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
        )
    # An Ubuntu box resolves by family too.
    assert (
        m.tarball_name("9.4.5.0", _CAT.box("mq-client", _UBUNTU), facts=X86)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
    )


def test_tarball_name_ubuntu_fat_box_tracks_host_arch():
    # #103 D10 (the arm64 crux): an un-pinned Ubuntu box has ONE name but TWO
    # arch-variant tarballs. Acquisition resolves the arch through the same
    # box_build_arch authority the box builder consumes, so it stages UbuntuLinuxARM64
    # on Apple Silicon and UbuntuLinuxX64 on the cloud — bake + acquire agree.
    for role in ("mq-client", "obs", "mq-nativeha", "pcmk"):
        entry = _CAT.box(role, _UBUNTU)
        assert (
            m.tarball_name("9.4.5.0", entry, facts=ARM)
            == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
        )
        assert (
            m.tarball_name("9.4.5.0", entry, facts=X86)
            == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
        )


def test_tarball_name_rhel_fat_box_stays_x64_on_arm():
    # RHEL is x86-pinned (#103 D11): even resolved under arm64 facts its tarball stays
    # LinuxX64 — the pin, not the host, decides. (Guards against over-eager host-tracking.)
    assert (
        m.tarball_name("9.4.5.0", _CAT.box("mq-nativeha", _RHEL), facts=ARM)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_probes_when_facts_none(monkeypatch):
    monkeypatch.setattr(m, "probe", lambda: ARM)
    assert m.tarball_name("9.4.5.0", _CAT.box("obs", _UBUNTU)).endswith("UbuntuLinuxARM64.tar.gz")


def test_tarball_name_unknown_family_raises():
    entry = _CAT.box("obs", _UBUNTU)
    alien = dataclasses.replace(entry, os=dataclasses.replace(entry.os, ref=OsRef("solaris", 11)))
    with pytest.raises(ValueError, match="no MQ tarball mapping for OS family 'solaris'"):
        m.tarball_name("9.4.5.0", alien, facts=X86)


def test_every_topology_mq_box_resolves_to_a_tarball():
    # #685 regression guard: every box the stack + commons prereq ensures enumerate must
    # resolve to a tarball, or the bootstrap's MQ-media prereq dies with ValueError
    # before any VM boots. Walk exactly those boxes and assert each resolves.
    from mqlab import cli
    from mqlab.stacks import lab_stacks

    boxes = set(cli._commons_mq_boxes())
    for stack in lab_stacks().values():
        boxes |= cli._stack_mq_boxes(stack)
    names = {b.name for b in boxes}
    nha_rhel = _CAT.default_os("nativeha-rhel-crr")
    assert f"mq-nativeha-{nha_rhel.token}" in names  # the #668 repoint is represented
    assert f"mq-nativeha-{_UBUNTU.token}" in names  # the #103 T6 repoint is represented
    for entry in sorted(boxes, key=lambda b: b.name):
        m.tarball_name(m.DEFAULT_MQ_VERSION, entry, facts=X86)  # must not raise


def test_obs_overlay_reads_shared_manifest(manifests):
    ov = m.obs_overlay()
    assert ov["prometheus_version"] == "2.53.2"
    assert ov["grafana_version"] == "11.1.0"
    assert ov["mq_exporter_ref"] == "v5.6.4"
    assert ov["data_prepper_version"] == "2.16.0"
    assert "mq_version" not in ov  # obs-only


def test_obs_overlay_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "manifests_root", lambda: tmp_path / "manifests")
    with pytest.raises(FileNotFoundError, match="observability.yaml"):
        m.obs_overlay()


def test_obs_overlay_non_mapping_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "manifests_root", lambda: tmp_path / "manifests")
    _write(tmp_path, "manifests/_shared/observability.yaml", "- a\n- b\n")
    with pytest.raises(ValueError, match="is not a mapping"):
        m.obs_overlay()


def test_committed_shared_obs_manifest_loads():
    # real manifests/_shared/observability.yaml, not the fixture
    ov = m.obs_overlay()
    assert ov["prometheus_version"]
    assert ov["mq_exporter_ref"]


def test_committed_manifest_pins_opensearch():
    # #829 (epic .github#149, logsearch tier): the shared obs manifest is the single
    # source for the OpenSearch + Dashboards version pin, so a re-bake never changes the
    # version under an existing snapshot. OpenSearch and its Dashboards share one upstream
    # version number. Real committed manifest, not the fixture.
    ov = m.obs_overlay()
    assert ov["opensearch_version"]
    assert ov["opensearch_dashboards_version"]


def test_committed_manifest_pins_data_prepper():
    # #939 (epic .github#149, logsearch tier): the shared obs manifest is the single
    # source for the Data Prepper connector version pin (the #826 spike proved 2.16.0),
    # so a re-bake never changes the shipper version. Real committed manifest, not the
    # fixture.
    ov = m.obs_overlay()
    assert ov["data_prepper_version"]


def test_default_mq_version_is_set():
    assert m.DEFAULT_MQ_VERSION
