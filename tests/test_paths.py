from __future__ import annotations

from mqlab.paths import lab_script, repo_root, runs_dir


def test_repo_root_contains_pyproject():
    assert (repo_root() / "pyproject.toml").is_file()


def test_repo_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert repo_root() == tmp_path


def test_runs_dir_is_under_build():
    assert runs_dir() == repo_root() / "build" / "runs"


def test_lab_script_points_at_lab_scripts_dir():
    assert lab_script("net-up.sh") == repo_root() / "lab" / "scripts" / "net-up.sh"
