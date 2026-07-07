import json

from typer.testing import CliRunner

from mqlab import cli

_TOPO = """
stacks:
  pcmk-ubuntu:
    cluster_group: pcmk_a
    groups: [pcmk_a, pcmk_b]
    qm: {vip: 10.10.1.200, vip_b: 10.10.2.200}
groups:
  pcmk_a: [pcmk-a1]
nodes:
  pcmk-a1: {nics: {net-data-a: 10.10.1.51}}
  svc-sim: {nics: {net-ext: 10.60.0.50}}
"""


def test_rest_render_writes_endpoints_json(tmp_path, monkeypatch):
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(_TOPO)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    result = CliRunner().invoke(cli.app, ["rest", "render"])
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "build" / "work" / "rest" / "endpoints.json").read_text())
    by_stack = {r["stack"]: r for r in data}
    assert by_stack["pcmk-ubuntu"]["endpoints"]["site-b"] == ["https://10.10.2.200:9443"]
    assert by_stack["svc-sim"]["kind"] == "counterparty"
