"""`mqlab qm reload <stack>`: restart the active QM with client reconnect (#1514).

Pins the CLI wiring (host search across the cluster group and the running DR groups, the
extra vars the playbook needs, the timeout, fail-loud exits), the per-arm topology verbs
(Native HA and RDQM run site-qm-reload.yml, Pacemaker refuses), and the IBM-documented
commands the role runs per mechanism.
"""

from __future__ import annotations

import io
import pathlib

import yaml
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, instances
from mqlab.instances import InstanceRecord
from mqlab.render import Renderer
from mqlab.stacks import lab_stacks
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import OsRef
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]
_ROLE = pathlib.Path("ansible/roles/mq-qm-reload/tasks")
_SITE = pathlib.Path("ansible/site-qm-reload.yml")


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("qm-reload", "20261015T000000Z")),
        pauser=_NoPause(),
    )


def _topo(reload_verb: str, *, cluster_hosts: str = "[nha-a1, nha-a2]") -> str:
    return (
        "nodes:\n"
        "  nha-a1: {nics: {net-mgmt: 10.50.0.11}}\n"
        "  nha-a2: {nics: {net-mgmt: 10.50.0.12}}\n"
        "  nha-b1: {nics: {net-mgmt: 10.50.0.14}}\n"
        f"groups:\n  nha_a: {cluster_hosts}\n  nha_b: [nha-b1]\n"
        "stacks:\n  nha-crr:\n    mechanism: native-ha\n    os_family: rhel\n    short: NHARC\n"
        "    cluster_group: nha_a\n    groups: [nha_a, nha_b]\n"
        # nha_x names no topology group: a DR group with no hosts is skipped, not searched
        "    dr_groups: [nha_b, nha_x]\n"
        "    qm: {}\n"
        "    verbs:\n"
        f"      qm-reload: {reload_verb}\n"
        "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
    )


_PLAYBOOK_TOPO = _topo("{ playbook: site-qm-reload.yml }")


def _seed(monkeypatch, tmp_path, topo=_PLAYBOOK_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def _probe(states):
    lines = [" Id   Name        State", "----"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


_ALL_UP = {"nha-a1": "running", "nha-a2": "running", "nha-b1": "running"}


def _invoke(monkeypatch, runner, *args):
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    return CliRunner().invoke(cli.app, ["qm", "reload", *args])


def _expected_argv(hosts: str, timeout: int = 300) -> list[str]:
    return [
        "ansible-playbook",
        "site-qm-reload.yml",
        "-e",
        "qm_stack=nha-crr",
        "-e",
        "qm_name=NHARCAPP",
        "-e",
        "qm_mechanism=native-ha",
        "-e",
        f"qm_hosts={hosts}",
        "-e",
        f"qm_reload_timeout={timeout}",
    ]


# --- CLI wiring ------------------------------------------------------------------------


def test_reload_searches_cluster_and_running_dr_groups(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 0, result.output
    play = runner.recorded[-1]
    assert play.argv == _expected_argv("nha_a:nha_b")
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_reload_passes_the_timeout(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    result = _invoke(monkeypatch, runner, "nha-crr", "--timeout", "60")
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv == _expected_argv("nha_a:nha_b", timeout=60)


def test_reload_rejects_a_zero_timeout(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr", "--timeout", "0")
    assert result.exit_code == 2
    assert runner.recorded == []


def test_reload_skips_a_dr_group_that_is_not_running(monkeypatch, tmp_path):
    # bootstrap --no-dr: site B is down, so only site A is searched (and the run says so).
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[_probe({"nha-a1": "running", "nha-a2": "running"}), ScriptedResult([])]
    )
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv == _expected_argv("nha_a")
    log = transcript_path("qm-reload", "20261015T000000Z").read_text()
    assert "nha_b: not running — not searched for the active NHARCAPP" in log
    assert "nha_x: not running — not searched for the active NHARCAPP" in log


def test_reload_cluster_group_down_exits_3_before_the_playbook(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"nha-a1": "running", "nha-b1": "running"})])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 3
    assert [c.argv for c in runner.recorded] == [[*_VIRSH, "list", "--all"]]
    log = transcript_path("qm-reload", "20261015T000000Z").read_text()
    assert "nha-a2: not running — run mqlab bootstrap nha-crr first" in log


def test_reload_playbook_failure_propagates(monkeypatch, tmp_path):
    # A timeout (or any failed check) in the playbook is a non-zero exit, never swallowed.
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([], exit_code=2)])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2


def test_reload_refuse_verb_exits_2_with_the_reason(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _topo("{ refuse: 'no honest reconnect-restart here.' }"))
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "mqlab qm reload nha-crr: refused — no honest reconnect-restart here." in result.stderr
    assert runner.recorded == []


def test_reload_unsupported_verb_kind_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _topo("{ cmd: 'endmqm -r {qm}' }"))
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "mqlab qm reload nha-crr: unsupported verb kind 'cmd'" in result.stderr
    assert runner.recorded == []


def test_reload_missing_verb_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PLAYBOOK_TOPO.replace("qm-reload:", "qm-other:"))
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "stack nha-crr does not implement verb 'qm-reload'" in result.stderr


def test_reload_cluster_group_without_hosts_exits_2(monkeypatch, tmp_path):
    # An empty host pattern would make ansible match nothing and exit 0: refuse it here.
    _seed(monkeypatch, tmp_path, _topo("{ playbook: site-qm-reload.yml }", cluster_hosts="[]"))
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "cluster group 'nha_a' has no hosts in the topology" in result.stderr
    assert runner.recorded == []


def test_reload_stack_without_a_cluster_group_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PLAYBOOK_TOPO.replace("    cluster_group: nha_a\n", ""))
    runner = RecordingRunner(results=[])
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "cluster group None has no hosts in the topology" in result.stderr
    assert runner.recorded == []


def test_reload_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["qm", "reload", "nope"])
    assert result.exit_code == 2
    assert "no stack" in result.output


def test_reload_refuses_a_live_stack_without_record(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 2
    assert "nha-crr is running without a version record" in result.stderr
    assert runner.recorded == []


def test_reload_runs_on_a_live_recorded_stack(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    instances.write_record(InstanceRecord("nha-crr", OsRef("rhel", 10), None, "t"))
    result = _invoke(monkeypatch, runner, "nha-crr")
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv[0] == "ansible-playbook"


# --- per-arm topology verbs (the committed lab/topology.yaml) --------------------------


def test_native_ha_and_rdqm_stacks_run_the_reload_playbook():
    stacks = lab_stacks()
    for name in ("nativeha-rhel-crr", "nativeha-ubuntu", "rdqm-rhel"):
        assert stacks[name].verbs["qm-reload"] == {"playbook": "site-qm-reload.yml"}, name


def test_pacemaker_stack_refuses_reload_with_a_reason():
    refuse = lab_stacks()["pcmk-ubuntu"].verbs["qm-reload"]["refuse"]
    assert "endmqm -w" in refuse
    assert "rdqm and native-ha" in refuse


# --- the playbook and role: IBM-documented commands only, fail loud --------------------


def _tasks(name: str) -> list[dict]:
    return yaml.safe_load((_ROLE / name).read_text())


def _flatten(tasks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for task in tasks:
        out.append(task)
        out.extend(_flatten(task.get("block", [])))
        out.extend(_flatten(task.get("rescue", [])))
    return out


def test_role_dispatches_only_documented_mechanisms():
    [guard] = [t for t in _tasks("main.yml") if "ansible.builtin.assert" in t]
    assert "qm_mechanism in ['native-ha', 'rdqm']" in guard["ansible.builtin.assert"]["that"]
    assert sorted(p.name for p in _ROLE.glob("*.yml")) == ["main.yml", "native-ha.yml", "rdqm.yml"]


def test_native_ha_reloads_the_mqmonitor_unit_not_a_raw_endmqm():
    tasks = _flatten(_tasks("native-ha.yml"))
    [reload] = [t for t in tasks if "ansible.builtin.systemd" in t]
    assert reload["ansible.builtin.systemd"] == {
        "name": "mqmonitor@{{ qm_name }}",
        "state": "reloaded",
    }
    assert reload["timeout"] == "{{ qm_reload_timeout | int }}"
    # IBM: the monitor restarts a QM ended behind its back, so no task may run endmqm.
    commands = [t["ansible.builtin.command"] for t in tasks if "ansible.builtin.command" in t]
    assert commands
    assert not any("endmqm" in str(c) for c in commands)


def test_native_ha_names_the_active_through_the_shared_detector():
    # Active detection lives in one task file (#891); the reload reuses it, inside the
    # rescued block so a missing Active fails with the reload's own message.
    [wait] = [t for t in _tasks("native-ha.yml") if "rescue" in t and "settle" in t["name"]]
    [find] = [t for t in wait["block"] if "ansible.builtin.include_tasks" in t]
    assert find["ansible.builtin.include_tasks"].endswith("/tasks/nativeha-find-active.yml")
    assert set(find["vars"]) == {"nha_group", "nha_qm", "nha_find_retries", "nha_find_delay"}
    assert "qm_reload_timeout" in find["vars"]["nha_find_retries"]


def test_rdqm_ends_with_reconnect_then_starts_on_the_primary():
    argvs = [
        t["ansible.builtin.command"]["argv"]
        for t in _flatten(_tasks("rdqm.yml"))
        if "ansible.builtin.command" in t
    ]
    mq = [a[-1] for a in argvs]
    assert mq[:2] == [
        "/opt/mqm/bin/endmqm -r -tp 29 -w {{ qm_name }}",
        "/opt/mqm/bin/strmqm {{ qm_name }}",
    ]
    assert mq[2] == "/opt/mqm/bin/dspmq -n -m {{ qm_name }}"


def test_every_wait_is_bounded_by_the_timeout_and_rescued_loudly():
    for name in ("native-ha.yml", "rdqm.yml"):
        tasks = _flatten(_tasks(name))
        polls = [t for t in tasks if "qm_reload_poll_delay" in str(t.get("delay", ""))]
        assert polls, name
        for poll in polls:
            assert "qm_reload_timeout" in poll["retries"], name
        blocks = [t for t in _tasks(name) if "rescue" in t]
        assert blocks, name
        for block in blocks:
            assert any("ansible.builtin.fail" in r for r in block["rescue"]), name


def test_until_conditionals_carry_no_backslash_escapes():
    # An `until` conditional applies string-escape processing that a template does not, so
    # a regex like '\b' or '\1' silently changes meaning there (#1514 sim finding).
    for name in ("native-ha.yml", "rdqm.yml"):
        for task in _flatten(_tasks(name)):
            assert "\\" not in str(task.get("until", "")), (name, task.get("name"))


def test_site_playbook_requires_inputs_and_exactly_one_running_instance():
    plays = yaml.safe_load(_SITE.read_text())
    guard, locate, restart = plays
    assert guard["hosts"] == "localhost"
    that = guard["tasks"][0]["ansible.builtin.assert"]["that"]
    for var in ("qm_stack", "qm_name", "qm_mechanism", "qm_hosts"):
        assert any(var in cond for cond in that), var
    assert locate["any_errors_fatal"] is True
    assert "length == 1" in str(locate["tasks"])
    assert restart["hosts"] == "qm_reload_active"
    assert restart["roles"] == ["mq-qm-reload"]
