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
        "manifests/_shared/observability.yaml",
        "prometheus: '2.53.2'\nnode_exporter: '1.8.2'\nloki: '3.1.0'\n"
        "alloy: '1.3.0'\ngrafana: '11.1.0'\nmq_metric_samples_ref: 'v5.6.4'\n",
    )
    return tmp_path


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
    # The fat RDQM box platform (#604) takes the same LinuxX64 tarball as rhel96-x86_64,
    # so the rdqm_a/rdqm_b nodes repointed at it still resolve their MQ media.
    assert (
        m.tarball_name("9.4.5.0", "mq-rdqm-rhel9")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_unknown_platform_raises():
    with pytest.raises(ValueError, match="no MQ tarball arch mapping"):
        m.tarball_name("9.4.5.0", "solaris-sparc")


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
