"""Every path that runs a stack's provision playbook gets bootstrap's provision env (#1502).

mqweb runs on every QM node, and its role fails loudly without the stack's
`mqweb_aes_passphrase` secret and MQWEB_STATE_DIR (#1500). `mqlab bootstrap` exported
both; `mqlab qm create <stack>` and lab/scripts/dr-provision.sh did not, so ungating
mqweb would have broken them. All three now take the env from one function,
cli._stack_provision_env:

- `qm create` passes it on the playbook's own subprocess (Command.env);
- `vm provision-env <stack>` prints it as shell exports, which dr-provision.sh evals.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, paths
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

REPO = Path(__file__).resolve().parents[1]
DR_PROVISION = REPO / "lab" / "scripts" / "dr-provision.sh"


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


_TOPO = (
    "nodes:\n"
    "  san-a:   {box: san, nics: {net-mgmt: 10.50.0.5}}\n"
    "  pcmk-a1: {box: pcmk, nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n"
    "    os_family: ubuntu\n    short: PCMK\n"
    "    cluster_group: pcmk_a\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    secrets: [pcmk_hacluster_password, mqweb_aes_passphrase]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
    "    verbs:\n"
    "      qm-create: { playbook: site-pcmk.yml }\n"
    "      qm-destroy: { playbook: site-pcmk-qm-down.yml }\n"
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


_ALL_UP = {"san-a": "running", "pcmk-a1": "running"}


# --- the shared env -----------------------------------------------------------------


def test_stack_provision_env_sources_every_secret_and_the_mqweb_state_dir(lab):
    runner = RecordingRunner(results=[ScriptedResult(["  hapw  "]), ScriptedResult(["aes"])])
    env = cli._stack_provision_env(_deps(runner), cli.lab_stacks()["pcmk-ubuntu"])
    assert env == {
        "PCMK_HACLUSTER_PASSWORD": "hapw",
        "MQWEB_AES_PASSPHRASE": "aes",
        "MQWEB_STATE_DIR": str(paths.state("mqweb")),
    }
    assert env["MQWEB_STATE_DIR"] == str(lab / "build" / "state" / "mqweb")
    # sourced through lab-secret.sh, in the stack's declared order
    assert [c.argv[-1] for c in runner.recorded] == [
        "pcmk_hacluster_password",
        "mqweb_aes_passphrase",
    ]


def test_stack_provision_env_fails_loud_when_a_secret_cannot_be_sourced(lab):
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=1)])
    with pytest.raises(typer.Exit):
        cli._stack_provision_env(_deps(runner), cli.lab_stacks()["pcmk-ubuntu"])


# --- qm create ----------------------------------------------------------------------


def test_qm_create_runs_the_playbook_with_the_provision_env(monkeypatch, lab):
    monkeypatch.setenv("MQWEB_STATE_DIR", "untouched")
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"v-{name}")
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 0, result.output
    play = runner.recorded[-1]
    assert play.argv[:2] == ["ansible-playbook", "site-pcmk.yml"]
    assert play.env == {
        "PCMK_HACLUSTER_PASSWORD": "v-pcmk_hacluster_password",
        "MQWEB_AES_PASSPHRASE": "v-mqweb_aes_passphrase",
        "MQWEB_STATE_DIR": str(paths.state("mqweb")),
    }
    # carried on the one subprocess, never leaked into mqlab's own environment
    assert os.environ["MQWEB_STATE_DIR"] == "untouched"
    assert "MQWEB_AES_PASSPHRASE" not in os.environ


def test_qm_create_with_members_down_sources_no_secrets(monkeypatch, lab):
    sourced: list[str] = []
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: sourced.append(name) or "x")
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 3
    assert sourced == []


def test_qm_create_stops_when_a_secret_cannot_be_sourced(monkeypatch, lab):
    runner = RecordingRunner(
        results=[_probe(_ALL_UP), ScriptedResult(["boom"], exit_code=1)]  # lab-secret.sh
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 2
    assert all(c.argv[0] != "ansible-playbook" for c in runner.recorded)


def test_qm_destroy_carries_no_provision_env(monkeypatch, lab):
    sourced: list[str] = []
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: sourced.append(name) or "x")
    runner = RecordingRunner(results=[_probe(_ALL_UP), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "destroy", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert runner.recorded[-1].env is None
    assert sourced == []


# --- vm provision-env ---------------------------------------------------------------


def test_vm_provision_env_prints_shell_exports_that_round_trip(monkeypatch, lab):
    tricky = "it's a $ecret; `x`"
    values = {"pcmk_hacluster_password": tricky, "mqweb_aes_passphrase": "aes"}
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: values[name])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))
    result = CliRunner().invoke(cli.app, ["vm", "provision-env", "pcmk-ubuntu"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == [
        "export PCMK_HACLUSTER_PASSWORD",
        "export MQWEB_AES_PASSPHRASE",
        "export MQWEB_STATE_DIR",
    ]
    # Evaluated by bash exactly as dr-provision.sh does, each value survives verbatim.
    probe = 'eval "$1"; printf "%s\\n" "$PCMK_HACLUSTER_PASSWORD" "$MQWEB_STATE_DIR"'
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
    result = CliRunner().invoke(cli.app, ["vm", "provision-env", "pcmk-ubuntu"])
    assert result.exit_code == 2
    assert "export" not in result.stdout


# --- lab/scripts/dr-provision.sh ----------------------------------------------------

_STUB_MQLAB = """#!/usr/bin/env bash
set -eu
echo "$PWD $*" >> "$STUB_LOG"
if [ "$*" = "vm provision-env pcmk-ubuntu" ]; then
  [ "${STUB_FAIL:-}" = 1 ] && { echo "lab-secret.sh failed" >&2; exit 2; }
  echo "export PCMK_HACLUSTER_PASSWORD='ha pw'"
  echo "export MQWEB_AES_PASSPHRASE=aes"
  echo "export MQWEB_STATE_DIR=/state/mqweb"
fi
"""

_STUB_PLAYBOOK = """#!/usr/bin/env bash
{
  echo "ARGS=$*"
  echo "PCMK=${PCMK_HACLUSTER_PASSWORD:-}"
  echo "AES=${MQWEB_AES_PASSPHRASE:-}"
  echo "STATE=${MQWEB_STATE_DIR:-}"
} > "$STUB_PLAY"
"""


def _dr_sandbox(tmp_path: Path, *, fail: bool) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "repo"
    (root / "lab" / "scripts").mkdir(parents=True)
    (root / "ansible").mkdir()
    shutil.copy2(DR_PROVISION, root / "lab" / "scripts" / "dr-provision.sh")
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    for name, body in (("mqlab", _STUB_MQLAB), ("ansible-playbook", _STUB_PLAYBOOK)):
        (stub_bin / name).write_text(body)
        (stub_bin / name).chmod(0o755)
    play = tmp_path / "play.out"
    env = {
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "mqlab.log"),
        "STUB_PLAY": str(play),
        **({"STUB_FAIL": "1"} if fail else {}),
    }
    result = subprocess.run(  # noqa: S603
        ["bash", str(root / "lab" / "scripts" / "dr-provision.sh"), "-e", "qm_app=PCMKAPP"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, play


def test_dr_provision_exports_the_provision_env_to_the_playbook(tmp_path):
    result, play = _dr_sandbox(tmp_path, fail=False)
    assert result.returncode == 0, result.stderr
    assert play.read_text().splitlines() == [
        "ARGS=site-pcmk.yml -e qm_app=PCMKAPP",
        "PCMK=ha pw",
        "AES=aes",
        "STATE=/state/mqweb",
    ]
    calls = (tmp_path / "mqlab.log").read_text().splitlines()
    repo = tmp_path / "repo"
    assert calls == [f"{repo} vm provision-env pcmk-ubuntu", f"{repo} vm inventory"]


def test_dr_provision_stops_when_the_provision_env_fails(tmp_path):
    result, play = _dr_sandbox(tmp_path, fail=True)
    assert result.returncode != 0
    assert "lab-secret.sh failed" in result.stderr
    assert not play.exists()  # the playbook never ran
