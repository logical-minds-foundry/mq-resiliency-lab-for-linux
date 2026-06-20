from __future__ import annotations

from mqlab.paths import lab_network, lab_script, repo_root, runs_dir


def test_repo_root_contains_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_repo_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path


def test_runs_dir_is_under_state_bucket():
    assert runs_dir() == repo_root() / "build" / "state" / "runs"


def test_lab_script_points_at_lab_scripts_dir():
    assert lab_script("net-up.sh") == repo_root() / "lab" / "scripts" / "net-up.sh"


def test_lab_network_points_at_lab_networks_xml():
    assert lab_network("net-data-a") == repo_root() / "lab" / "networks" / "net-data-a.xml"


def test_bucket_primitives(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab import paths

    assert paths.build_root() == tmp_path / "build"
    assert paths.cache("mq") == tmp_path / "build" / "cache" / "mq"
    assert paths.state("snapshots") == tmp_path / "build" / "state" / "snapshots"
    assert paths.work("inventory.ini") == tmp_path / "build" / "work" / "inventory.ini"
    assert paths.temp_dir() == tmp_path / "build" / "temp"


def test_named_helpers_point_into_buckets(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab import paths

    build = tmp_path / "build"
    assert paths.runs_dir() == build / "state" / "runs"
    assert paths.reports_dir() == build / "state" / "reports"
    assert paths.selection_state_path("s") == build / "state" / "manifests" / "s.yaml"
    assert paths.inventory_path() == build / "work" / "inventory.ini"
    assert paths.resolved_topology_path() == build / "work" / "lab" / "topology.resolved.yaml"
    assert paths.box_versions_path() == build / "work" / "box-versions.json"
    assert paths.mq_cache_dir() == build / "cache" / "mq"
