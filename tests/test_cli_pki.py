from __future__ import annotations

import io

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
        transcript=Transcript(transcript_path("pki", "20260616T000000Z")),
        pauser=_NoPause(),
    )


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text("nodes: {}\ngroups: {}\nsetups: {}\n")


def test_pki_ensure_runs_provider_playbook_locally(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["pki", "ensure"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == ["ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,"]
    assert str(play.cwd).endswith("/ansible")


def test_pki_issue_passes_pki_only_extra_var(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["pki", "issue", "QMPCMK"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible-playbook",
        "site-pki.yml",
        "-c",
        "local",
        "-i",
        "localhost,",
        "-e",
        "pki_only=QMPCMK",
    ]


def test_pki_list_prints_entities_from_vars(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    (tmp_path / "ansible" / "vars").mkdir(parents=True)
    (tmp_path / "ansible" / "vars" / "pki-entities.yml").write_text(
        "pki_cas: {}\n"
        "pki_entities:\n"
        "  - {cn: QMPCMK, org: app-org, ou: messaging, kind: personal}\n"
    )
    result = CliRunner().invoke(cli.app, ["pki", "list"])
    assert result.exit_code == 0
    assert "QMPCMK" in result.output
    assert "app-org" in result.output
