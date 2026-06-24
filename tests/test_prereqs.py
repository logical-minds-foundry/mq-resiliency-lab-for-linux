"""_ensure_prereqs — the single place that ensures a setup's fresh-volume prerequisites
(galaxy collections + MQ tarball + PKI), in dependency order. (#343)"""

from __future__ import annotations

import io

from rich.console import Console

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner: RecordingRunner) -> cli.Deps:
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("prereqs", "20260610T000000Z")),
        pauser=_NoPause(),
    )


def _seed(tmp_path) -> None:
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  obs: {nics: {net-mgmt: 10.50.0.2}}\n  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n  obs_box: [obs]\n  probe: [mon-probe]\n"
        "setups:\n  monitoring:\n    groups: [obs_box, probe]\n"
    )


def test_ensure_prereqs_runs_mq_then_galaxy_then_pki(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    ensured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli, "ensure_mq_tarballs", lambda setup, version, *a, **k: ensured.append((setup, version))
    )
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])  # galaxy, pki
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    cli._ensure_prereqs("monitoring")

    # MQ tarball ensured at the setup's default version (manifest-less)
    assert ensured == [("monitoring", cli.DEFAULT_MQ_VERSION)]
    argvs = [c.argv for c in runner.recorded]
    # galaxy collections installed before the PKI play (which needs community.crypto)
    assert argvs[0][:3] == ["ansible-galaxy", "collection", "install"]
    assert "site-pki.yml" in argvs[1]
