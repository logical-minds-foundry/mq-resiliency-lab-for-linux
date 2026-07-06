from __future__ import annotations

import io
import json

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("obs", "20260610T000000Z")),
        pauser=_NoPause(),
    )


def _seed_monitoring(tmp_path):
    # The surviving obs render commands (targets/dashboard/net-state/reach-peers)
    # read nodes:/groups: straight from topology — no setups:/stacks: needed. Every
    # group host also needs a node with a net-mgmt IP: render_inventory / lab_dashboard
    # raise on a group that references an undefined host. dashboard.ROWS references
    # pcmk_a/san_a/pcmk_b/san_b/rdqm_a/rdqm_b/app/svc/obs_box/probe, so all are seeded.
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "  san-b: {nics: {net-mgmt: 10.50.0.6}}\n"
        "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
        "  pcmk-b1: {nics: {net-mgmt: 10.50.0.61}}\n"
        "  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "  rdqm-b1: {nics: {net-mgmt: 10.50.0.41}}\n"
        "  svc-sim: {nics: {net-mgmt: 10.50.0.50}}\n"
        "  app-client: {nics: {net-mgmt: 10.50.0.60}}\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "  rdqm_a: [rdqm-a1]\n  rdqm_b: [rdqm-b1]\n"
        "  svc: [svc-sim]\n  app: [app-client]\n"
        "  obs_box: [obs]\n  probe: [mon-probe]\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    )


# --- targets: renders the file_sd JSON under build/ from topology ---


def test_obs_targets_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "targets"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "work" / "prometheus" / "targets" / "node.json"
    hosts = {e["labels"]["host"] for e in json.loads(written.read_text())}
    assert {"obs", "mon-probe"} <= hosts


def test_obs_targets_also_writes_mq_exporters_deployment_list(monkeypatch, tmp_path):
    # `obs targets` also renders the per-stack mq-exporter deployment list that
    # site-obs.yml loops over. The observe phase runs this render step, then passes
    # the file to site-obs.yml as `-e @<file>`; #423 wired that only into `obs up`,
    # so the observe phase failed on an undefined `mq_exporters` (#434). Rendering it
    # here pins both call sites to the same file.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    # add an observable stack so the deployment list is non-empty (app + svc instance)
    topo = tmp_path / "lab" / "topology.yaml"
    stack_yaml = (
        "stacks:\n"
        "  pcmk-ubuntu:\n"
        "    short: PCMK\n"
        "    qm: {vip: 10.10.1.200}\n"
        "    alloc: {exporter_app_port: 9157}\n"
    )
    topo.write_text(topo.read_text() + stack_yaml)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "targets"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "work" / "obs" / "mq-exporters.json"
    instances = json.loads(written.read_text())["mq_exporters"]
    assert {i["role"] for i in instances} == {"app", "svc"}
    assert {i["port"] for i in instances} == {9157, 9158}


def test_obs_targets_stack_scopes_only_the_exporter_deployment_list(monkeypatch, tmp_path):
    # #503: --stack scopes the mq-exporter DEPLOYMENT list to that stack (+ the shared
    # svc), so observing one stack never deploys another stack's crash-looping exporter
    # unit — while the ibmmq SCRAPE targets stay full-topology (a down target is benign).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    topo = tmp_path / "lab" / "topology.yaml"
    stacks_yaml = (
        "stacks:\n"
        "  pcmk-ubuntu:\n"
        "    short: PCMK\n"
        "    qm: {vip: 10.10.1.200}\n"
        "    alloc: {exporter_app_port: 9157}\n"
        "  rdqm-rhel:\n"
        "    short: RDQM\n"
        "    qm: {vip: 10.10.1.201}\n"
        "    alloc: {exporter_app_port: 9159}\n"
    )
    topo.write_text(topo.read_text() + stacks_yaml)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "targets", "--stack", "pcmk-ubuntu"])

    assert result.exit_code == 0
    # deployment list: only pcmk-ubuntu's app + the shared svc (commons), NOT rdqm-rhel
    deploy = json.loads((tmp_path / "build" / "work" / "obs" / "mq-exporters.json").read_text())
    assert {i["stack"] for i in deploy["mq_exporters"]} == {"pcmk-ubuntu", "commons"}
    # the ibmmq scrape targets stay full-topology (both stacks + svc)
    ibmmq = json.loads(
        (tmp_path / "build" / "work" / "prometheus" / "targets" / "ibmmq.json").read_text()
    )
    assert {e["labels"]["stack"] for e in ibmmq} == {"pcmk-ubuntu", "rdqm-rhel", "commons"}


# --- dashboard: renders the fleet board + the cluster cockpit boards from topology ---


def test_obs_dashboard_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-status.json"
    assert json.loads(written.read_text())["uid"] == "lab-fleet-node"


def test_obs_dashboard_also_renders_the_rdqm_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-rdqm-cluster.json"
    assert json.loads(board.read_text())["uid"] == "lab-rdqm-cluster"


def test_obs_dashboard_also_renders_the_per_qm_boards(monkeypatch, tmp_path):
    # #489: one lab-qm-<short>.json per provisioned app QM, alongside the messaging boards.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    topo = tmp_path / "lab" / "topology.yaml"
    topo.write_text(
        topo.read_text() + "stacks:\n"
        "  pcmk-ubuntu:\n"
        "    mechanism: pacemaker-san\n"
        "    os: ubuntu\n"
        "    short: PCMK\n"
        "    qm: {vip: 10.10.1.200}\n"
        "    provision: ansible/pcmk.yml\n"
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-qm-pcmk.json"
    assert json.loads(board.read_text())["uid"] == "lab-qm-pcmk"


def test_obs_dashboard_also_renders_the_watcher_board(monkeypatch, tmp_path):
    # The Watcher (#488) — the lab-state front-door board — renders alongside the
    # other cockpits when `obs dashboard` runs.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-watcher.json"
    assert json.loads(board.read_text())["uid"] == "lab-watcher"


# --- open: prints the workstation URL and explains the automatic forward ---


def test_obs_open_prints_workstation_url_and_automatic_forward():
    result = CliRunner().invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    # the workstation browses plain localhost:3000 (auto-forwarded), plus the
    # in-VM direct URL for reference. The Watcher (#488) is the front door;
    # the predecessor Fleet — Node Health board is still reachable.
    assert "http://localhost:3000/d/lab-watcher" in result.stdout
    assert "http://10.50.0.2:3000/d/lab-watcher" in result.stdout
    assert "http://localhost:3000/d/lab-fleet-node" in result.stdout
    assert "/explore" in result.stdout
    assert "mqlab-requester" in result.stdout
    # the forward is automatic and anonymous now — the stale manual-tunnel /
    # admin-login guidance must be gone (#264, #258)
    assert "automatic" in result.stdout
    assert "ssh -F" not in result.stdout
    assert "-L 3000:" not in result.stdout
    assert "admin / admin" not in result.stdout


# --- net-state: emit lab_network_state textfile metrics from virsh net-list ---


def test_obs_net_state_emits_textfile_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "networks").mkdir(parents=True)
    (tmp_path / "lab" / "networks" / "net-hb-a.xml").write_text("<network/>")
    runner = RecordingRunner(
        results=[
            ScriptedResult(
                [
                    " Name      State    Autostart   Persistent",
                    "----------------------------------------------",
                    " net-hb-a   active   yes         yes",
                ]
            )
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "net-state"])

    assert result.exit_code == 0
    assert 'lab_network_state{network="net-hb-a"} 2' in result.stdout


# --- reach-peers: render the host -> net -> peers map under build/ from topology ---


def test_obs_reach_peers_writes_build_json(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  pcmk-a1: {nics: {net-hb-a: 172.16.1.51}}\n"
        "  pcmk-a2: {nics: {net-hb-a: 172.16.1.52}}\n"
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "reach-peers"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / "build" / "work" / "obs" / "reach-peers.json").read_text())
    assert data["pcmk-a1"]["net-hb-a"][0]["peer"] == "pcmk-a2"
