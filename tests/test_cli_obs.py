from __future__ import annotations

import json

from typer.testing import CliRunner

from mqlab import cli

runner = CliRunner()


def test_obs_targets_writes_and_echoes(monkeypatch, tmp_path):
    # Point repo_root at a tmp repo carrying a minimal topology.
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "groups:\n"
        "  obs: [obs]\n"
    )
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("mqlab.scrape.repo_root", lambda: tmp_path)

    result = runner.invoke(cli.app, ["obs", "targets"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "prometheus" / "targets" / "node.json"
    assert json.loads(written.read_text())[0]["labels"]["host"] == "obs"
    assert "10.50.0.2:9100" in result.stdout


def test_obs_open_prints_url_and_tunnel(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    result = runner.invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert "http://10.50.0.2:3000" in result.stdout
    assert "ssh -L" in result.stdout


def test_obs_up_steps_render_then_create_then_provision(monkeypatch, tmp_path):
    # Rendering happens eagerly when steps are built, so point repo_root at a
    # tmp repo carrying a minimal topology.
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  obs: {nics: {net-mgmt: 10.50.0.2}}\ngroups:\n  obs: [obs]\n"
    )
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("mqlab.scrape.repo_root", lambda: tmp_path)

    steps = cli._obs_up_steps()
    labels = [s.label for s in steps]
    assert labels == ["render scrape targets", "monitoring create", "provision monitoring"]
    # provisioning targets the monitoring setup's playbook via the rendered inventory
    provision = steps[-1].command.argv
    assert "ansible-playbook" in provision
    assert "ansible/site-obs.yml" in " ".join(provision)
