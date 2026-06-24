"""Coverage for the version-manifest wiring helpers in cli.py (#266)."""

from __future__ import annotations

import json

from mqlab import cli


def _repo(tmp_path):
    (tmp_path / "manifests" / "s").mkdir(parents=True)
    (tmp_path / "manifests" / "s" / "default.yaml").write_text(
        "mq:\n  version: '9.4.5.0'\nos:\n  box: b\n  box_version: '1'\n"
    )
    (tmp_path / "manifests" / "_shared").mkdir(parents=True)
    (tmp_path / "manifests" / "_shared" / "observability.yaml").write_text(
        "prometheus: p\nnode_exporter: ne\nloki: l\n"
        "alloy: a\ngrafana: g\nmq_metric_samples_ref: r\n"
    )
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "boxes:\n  plat: {box: b}\nnodes:\n  n1: {platform: plat}\n"
        "groups:\n  g: [n1]\nsetups:\n  s: {groups: [g]}\n"
    )
    return tmp_path


def test_apply_manifest_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert cli._apply_manifest("nope") is None
    assert cli._manifest_args("nope") == []
    assert cli._manifest_id("nope") == ""
    assert cli._manifest_digest_paths("nope") == []


def test_apply_manifest_drives_overlay_box_versions_and_id(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "ensure_mq_tarballs", lambda *a, **k: [])

    op = cli._apply_manifest("s", at_create=True)  # bvf absent -> {} branch
    assert op is not None
    assert json.loads(op.read_text())["mq_version"] == "9.4.5.0"
    assert json.loads((tmp_path / "build" / "work" / "box-versions.json").read_text()) == {
        "plat": "1"
    }

    cli._apply_manifest("s", at_create=True)  # bvf exists -> merge branch

    assert cli._manifest_args("s")[0] == "-e"  # at_create=False branch (reads the pin)
    assert cli._manifest_id("s") == "s/default"
    assert cli._manifest_digest_paths("s")  # selection file now exists


def test_obs_manifest_args_present_then_absent(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert cli._obs_manifest_args()[0] == "-e"
    (tmp_path / "manifests" / "_shared" / "observability.yaml").unlink()
    assert cli._obs_manifest_args() == []


def test_resolve_mq_version_uses_manifest_pin(tmp_path, monkeypatch):
    # A setup WITH a manifest takes its pinned mq version. (#333)
    _repo(tmp_path)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert cli.resolve_mq_version("s") == "9.4.5.0"


def test_resolve_mq_version_defaults_when_no_manifest(tmp_path, monkeypatch):
    # A setup WITHOUT a manifest (e.g. monitoring) falls back to the repo default,
    # so its MQ artifact can still be ensured. (#333)
    _repo(tmp_path)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert cli.resolve_mq_version("monitoring") == cli.DEFAULT_MQ_VERSION
