"""Every path that runs a stack's provision playbook gets bootstrap's provision env (#1502).

mqweb runs on every QM node, and its role fails loudly without the stack's
`mqweb_aes_passphrase` secret and MQWEB_STATE_DIR (#1500). `mqlab bootstrap` exported
both; `mqlab qm create <stack>` did not, so ungating mqweb would have broken it. Every
path now takes the env from one function, cli._stack_provision_env:

- `qm create` passes it on the playbook's own subprocess (Command.env);
- `vm provision-env <stack>` prints it as shell exports, for an operator script to eval.
"""

from __future__ import annotations

import io
import os
import subprocess
from typing import TYPE_CHECKING

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, paths
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

if TYPE_CHECKING:
    from pathlib import Path



class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("qm", "20260611T000000Z")),
        pauser=_NoPause(),
    )


# Two secrets (the second a fixture-only name) so the declared sourcing order shows.
_TOPO = (
    "nodes:\n"
    "  nha-ubuntu-a1: {box: mq-nativeha, nics: {net-mgmt: 10.50.0.11}}\n"
    "  nha-ubuntu-b1: {box: mq-nativeha, nics: {net-mgmt: 10.50.0.14}}\n"
    "groups:\n  nha_ubuntu_a: [nha-ubuntu-a1]\n  nha_ubuntu_b: [nha-ubuntu-b1]\n"
    "stacks:\n  nativeha-ubuntu:\n    mechanism: native-ha\n"
    "    os_family: ubuntu\n    short: NHAU\n"
    "    cluster_group: nha_ubuntu_a\n    groups: [nha_ubuntu_a, nha_ubuntu_b]\n"
    "    provision: ansible/site-nativeha-ubuntu.yml\n"
    "    secrets: [cluster_password, mqweb_aes_passphrase]\n"
    "    qm: {}\n"
    "    verbs:\n"
    "      qm-create: { playbook: site-nativeha-ubuntu.yml }\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)


@pytest.fixture
def lab(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_TOPO)
    return tmp_path


def _probe(states):
    lines = [" Id   Name        State", "----"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


_ALL_UP = {"nha-ubuntu-a1": "running", "nha-ubuntu-b1": "running"}


# --- the shared env -----------------------------------------------------------------


def test_stack_provision_env_sources_every_secret_and_the_mqweb_state_dir(lab):
    runner = RecordingRunner(results=[ScriptedResult(["  hapw  "]), ScriptedResult(["aes"])])
    env = cli._stack_provision_env(_deps(runner), cli.lab_stacks()["nativeha-ubuntu"])
    assert env == {
        "CLUSTER_PASSWORD": "hapw",
        "MQWEB_AES_PASSPHRASE": "aes",
        "MQWEB_STATE_DIR": str(paths.state("mqweb")),
    }
    assert env["MQWEB_STATE_DIR"] == str(lab / "build" / "state" / "mqweb")
    # sourced through lab-secret.sh, in the stack's declared order
    assert [c.argv[-1] for c in runner.recorded] == [
        "cluster_password",
        "mqweb_aes_passphrase",
    ]


def test_stack_provision_env_fails_loud_when_a_secret_cannot_be_sourced(lab):
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=1)])
    with pytest.raises(typer.Exit):
        cli._stack_provision_env(_deps(runner), cli.lab_stacks()["nativeha-ubuntu"])


# --- qm create ----------------------------------------------------------------------


def test_qm_create_runs_the_playbook_with_the_provision_env(monkeypatch, lab):
    monkeypatch.setenv("MQWEB_STATE_DIR", "untouched")
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"v-{name}")
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "nativeha-ubuntu"])
    assert result.exit_code == 0, result.output
    play = runner.recorded[-1]
    assert play.argv[:2] == ["ansible-playbook", "site-nativeha-ubuntu.yml"]
    assert play.env == {
        "CLUSTER_PASSWORD": "v-cluster_password",
        "MQWEB_AES_PASSPHRASE": "v-mqweb_aes_passphrase",
        "MQWEB_STATE_DIR": str(paths.state("mqweb")),
    }
    # carried on the one subprocess, never leaked into mqlab's own environment
    assert os.environ["MQWEB_STATE_DIR"] == "untouched"
    assert "MQWEB_AES_PASSPHRASE" not in os.environ


def test_qm_create_with_members_down_sources_no_secrets(monkeypatch, lab):
    sourced: list[str] = []
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: sourced.append(name) or "x")
    runner = RecordingRunner(results=[_probe({"nha-ubuntu-a1": "running"})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "nativeha-ubuntu"])
    assert result.exit_code == 3
    assert sourced == []


def test_qm_create_stops_when_a_secret_cannot_be_sourced(monkeypatch, lab):
    runner = RecordingRunner(
        results=[_probe(_ALL_UP), ScriptedResult(["boom"], exit_code=1)]  # lab-secret.sh
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "nativeha-ubuntu"])
    assert result.exit_code == 2
    assert all(c.argv[0] != "ansible-playbook" for c in runner.recorded)


# --- vm provision-env ---------------------------------------------------------------


def test_vm_provision_env_prints_shell_exports_that_round_trip(monkeypatch, lab):
    tricky = "it's a $ecret; `x`"
    values = {"cluster_password": tricky, "mqweb_aes_passphrase": "aes"}
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: values[name])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))
    result = CliRunner().invoke(cli.app, ["vm", "provision-env", "nativeha-ubuntu"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == [
        "export CLUSTER_PASSWORD",
        "export MQWEB_AES_PASSPHRASE",
        "export MQWEB_STATE_DIR",
    ]
    # Evaluated by bash with `eval`, each value survives verbatim.
    probe = 'eval "$1"; printf "%s\\n" "$CLUSTER_PASSWORD" "$MQWEB_STATE_DIR"'
    out = subprocess.run(  # noqa: S603
        ["bash", "-c", probe, "probe", result.stdout],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.splitlines() == [tricky, str(paths.state("mqweb"))]


def test_vm_provision_env_unknown_stack_exits_2(lab):
    result = CliRunner().invoke(cli.app, ["vm", "provision-env", "nope"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "no stack named 'nope'" in result.stderr


def test_vm_provision_env_prints_nothing_when_a_secret_fails(monkeypatch, lab):
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["vm", "provision-env", "nativeha-ubuntu"])
    assert result.exit_code == 2
    assert "export" not in result.stdout
