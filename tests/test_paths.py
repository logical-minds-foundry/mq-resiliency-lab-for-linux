from __future__ import annotations

from pathlib import Path

import pytest

from mqlab import paths
from mqlab.paths import lab_network, lab_script, repo_root, runs_dir


def test_repo_root_contains_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_repo_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path


@pytest.mark.real_build_root
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
    assert paths.versions_catalog_path() == tmp_path / "lab" / "versions.yaml"
    assert paths.selection_state_path("s") == build / "state" / "manifests" / "s.yaml"
    assert paths.instances_dir() == build / "state" / "instances"
    assert paths.inventory_path() == build / "work" / "inventory.ini"
    assert paths.resolved_topology_path() == build / "work" / "lab" / "topology.resolved.yaml"
    assert paths.mq_cache_dir() == build / "cache" / "mq"
    assert not hasattr(paths, "san_deb_cache_dir")  # the SAN deb cache is retired (#1278)


def test_suite_never_resolves_the_checkouts_own_build_root():
    # #1569: tests build real Transcripts under build/state/runs/. conftest redirects the
    # checkout's build/ so the suite cannot create a real (forked) build/state in a
    # worktree; every bucket helper must follow that redirect.
    checkout_build = Path(__file__).resolve().parents[1] / "build"
    for bucket_path in (paths.state("runs"), paths.cache(), paths.work(), paths.temp_dir()):
        assert checkout_build not in bucket_path.parents


def test_suite_isolation_follows_a_tests_own_repo_root(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert paths.state("runs") == tmp_path / "build" / "state" / "runs"
