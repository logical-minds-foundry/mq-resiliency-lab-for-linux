from __future__ import annotations

import pytest

from mqlab import manifest as m
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)


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
        "alloy: '1.3.0'\ngrafana: '11.1.0'\nmq_metric_samples_ref: 'v5.6.4'\n",
    )
    return tmp_path


def test_tarball_name_maps_version_and_arch():
    # Arch-explicit / x86-pinned platforms are facts-independent — the pin decides.
    assert (
        m.tarball_name("9.4.5.0", "ubuntu2404-arm64", facts=X86)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    )
    assert (
        m.tarball_name("9.4.5.0", "ubuntu2404-x86_64", facts=ARM)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
    )
    assert (
        m.tarball_name("9.4.5.0", "rhel96-x86_64", facts=X86)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )
    # The fat RDQM box platform (#604) takes the same LinuxX64 tarball as rhel96-x86_64,
    # so the rdqm_a/rdqm_b nodes repointed at it still resolve their MQ media.
    assert (
        m.tarball_name("9.4.5.0", "mq-rdqm-rhel9", facts=X86)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )
    # The fat native-HA RHEL box platform (#667/#668) takes the same LinuxX64 tarball as
    # rhel96-x86_64, so the nha-rhel-* nodes repointed at it resolve their MQ media.
    assert (
        m.tarball_name("9.4.5.0", "mq-nativeha-rhel9", facts=X86)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_ubuntu_fat_box_tracks_host_arch():
    # #103 D10 (the arm64 crux): the un-pinned Ubuntu fat boxes (obs/mq-ubuntu2404) have
    # ONE platform name but TWO arch-variant tarballs. Acquisition resolves the arch
    # through the same box_build_arch authority the box builder consumes, so it stages
    # UbuntuLinuxARM64 on Apple Silicon and UbuntuLinuxX64 on the cloud — bake + acquire
    # agree by construction, never the baked-in x86 literal of the old box-name mapping.
    for platform in ("mq-ubuntu2404", "obs-ubuntu2404", "mq-nativeha-ubuntu"):
        assert (
            m.tarball_name("9.4.5.0", platform, facts=ARM)
            == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
        )
        assert (
            m.tarball_name("9.4.5.0", platform, facts=X86)
            == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
        )


def test_tarball_name_rhel_fat_box_stays_x64_on_arm():
    # RHEL is x86-pinned (#103 D11): even resolved under arm64 facts its tarball stays
    # LinuxX64 — the pin, not the host, decides. (Guards against over-eager host-tracking.)
    assert (
        m.tarball_name("9.4.5.0", "mq-nativeha-rhel9", facts=ARM)
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_unknown_platform_raises():
    with pytest.raises(ValueError, match="no MQ tarball arch mapping"):
        m.tarball_name("9.4.5.0", "solaris-sparc")


def test_every_topology_mq_platform_resolves_to_a_tarball():
    # #685 regression guard: repointing nodes to a NEW fat-box platform (as #668 did for
    # mq-nativeha-rhel9) must also add it to _ARCH_SUFFIX, or the bootstrap's MQ-media
    # prereq dies with ValueError before any VM boots. The topology-resolution tests never
    # exercise this path — only a bootstrap (or this test) does. Walk exactly the platforms
    # the stack + commons prereq ensures enumerate and assert each resolves.
    from mqlab import cli
    from mqlab.stacks import lab_stacks

    platforms = set(cli._commons_mq_platforms())
    for stack in lab_stacks().values():
        platforms |= cli._stack_mq_platforms(stack)
    assert "mq-nativeha-rhel9" in platforms  # the #668 repoint is represented
    assert "mq-nativeha-ubuntu" in platforms  # the #103 T6 repoint is represented
    for platform in sorted(platforms):
        m.tarball_name(m.DEFAULT_MQ_VERSION, platform)  # must not raise ValueError


def test_obs_overlay_reads_shared_manifest(manifests):
    ov = m.obs_overlay()
    assert ov["prometheus_version"] == "2.53.2"
    assert ov["grafana_version"] == "11.1.0"
    assert ov["mq_exporter_ref"] == "v5.6.4"
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


def test_default_mq_version_is_set():
    assert m.DEFAULT_MQ_VERSION
