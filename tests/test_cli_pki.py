from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

# Non-QM entity CNs that must always be present in the rendered output.
_FIXED_CNS = {"app-client", "mq_prometheus", "mqweb", "pymqrest", "svc-responder"}

# The full static entity set that Phase 1 must reproduce exactly.
_STATIC_CNS = {"QMPCMK", "QMRDQM", "QMNATIVE", "QMSVC"} | _FIXED_CNS

# Minimal topology covering all three app-org QMs and the shared svc-org QM.
_PKI_TOPO = (
    "nodes: {}\n"
    "groups: {}\n"
    "setups:\n"
    "  pcmk_arm:\n"
    "    groups: []\n"
    "    qm: { name: QMPCMK, svc: QMSVC }\n"
    "  rdqm_arm:\n"
    "    groups: []\n"
    "    qm: { name: QMRDQM, svc: QMSVC }\n"
    "  nativeha_arm:\n"
    "    groups: []\n"
    "    qm: { name: QMNATIVE, svc: QMSVC }\n"
)


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


def _seed(monkeypatch, tmp_path, topo="nodes: {}\ngroups: {}\nsetups: {}\n"):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def test_pki_ensure_runs_provider_playbook_locally(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["pki", "ensure"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == ["ansible-playbook", "site-pki.yml", "-c", "local", "-i", "localhost,"]
    assert str(play.cwd).endswith("/ansible")
    # Entities file must have been rendered before the playbook ran.
    assert (tmp_path / "build" / "work" / "pki" / "entities.json").exists()


def test_pki_issue_passes_pki_only_extra_var(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
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
    # Entities file must have been rendered before the playbook ran.
    assert (tmp_path / "build" / "work" / "pki" / "entities.json").exists()


def test_pki_list_prints_entities_from_topology(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    result = CliRunner().invoke(cli.app, ["pki", "list"])
    assert result.exit_code == 0
    assert "QMPCMK" in result.output
    assert "app-org" in result.output


# --- _render_pki_entities ---------------------------------------------------


def test_render_pki_entities_includes_derived_qm_cns(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    data = json.loads(path.read_text())
    cns = {e["cn"]: e["org"] for e in data}
    assert cns["QMPCMK"] == "app-org"
    assert cns["QMSVC"] == "svc-org"


def test_render_pki_entities_includes_all_qm_cns(monkeypatch, tmp_path):
    """All three distinct app-org QMs and the shared svc-org QM must appear."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    data = json.loads(path.read_text())
    cns = {e["cn"] for e in data}
    assert {"QMPCMK", "QMRDQM", "QMNATIVE", "QMSVC"}.issubset(cns)


def test_render_pki_entities_preserves_fixed_non_qm_entities(monkeypatch, tmp_path):
    """Fixed non-QM entity CNs must be present verbatim."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    assert _FIXED_CNS.issubset(cns)


def test_render_pki_entities_full_set_matches_static_list(monkeypatch, tmp_path):
    """Phase 1: the rendered CN set must equal today's static pki-entities.yml exactly."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    assert cns == _STATIC_CNS


def test_render_pki_entities_correct_orgs_and_shape(monkeypatch, tmp_path):
    """Each entity must carry the correct org, ou, kind, and trust fields."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    data = json.loads(cli._render_pki_entities().read_text())
    by_cn = {e["cn"]: e for e in data}

    # app-org QMs
    for qm_cn in ("QMPCMK", "QMRDQM", "QMNATIVE"):
        e = by_cn[qm_cn]
        assert e["org"] == "app-org"
        assert e["ou"] == "messaging"
        assert e["kind"] == "personal"
        assert e["trust"] == ["svc-org"]

    # svc-org QM
    e = by_cn["QMSVC"]
    assert e["org"] == "svc-org"
    assert e["ou"] == "messaging"
    assert e["kind"] == "personal"
    assert e["trust"] == ["app-org"]


def test_render_pki_entities_dedupes_repeated_qm_names(monkeypatch, tmp_path):
    """When multiple setups share the same QM name, only one entity is emitted."""
    topo = (
        "nodes: {}\ngroups: {}\n"
        "setups:\n"
        "  arm_a:\n    groups: []\n    qm: { name: QMPCMK }\n"
        "  arm_b:\n    groups: []\n    qm: { name: QMPCMK }\n"
    )
    _seed(monkeypatch, tmp_path, topo)
    data = json.loads(cli._render_pki_entities().read_text())
    qmpcmk_count = sum(1 for e in data if e["cn"] == "QMPCMK")
    assert qmpcmk_count == 1


def test_render_pki_entities_writes_to_work_pki(monkeypatch, tmp_path):
    """The rendered file must land at build/work/pki/entities.json."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    assert path == tmp_path / "build" / "work" / "pki" / "entities.json"


def test_render_pki_entities_no_qm_setups_yields_only_fixed(monkeypatch, tmp_path):
    """When no setup has a QM config, only the fixed non-QM entities are emitted."""
    _seed(monkeypatch, tmp_path)  # empty topology — no setups
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    assert cns == _FIXED_CNS


def test_render_pki_entities_setup_without_qm_is_skipped(monkeypatch, tmp_path):
    """A setup that declares no QM config must not add any entities."""
    topo = "nodes: {}\ngroups: {}\nsetups:\n  bare:\n    groups: []\n"
    _seed(monkeypatch, tmp_path, topo)
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    # No QM CNs; only the fixed non-QM set.
    assert cns == _FIXED_CNS


# --- _pki_ensure_step prereqs path (#351) -----------------------------------


def test_pki_ensure_step_renders_entities_before_playbook(monkeypatch, tmp_path):
    """_pki_ensure_step() must call _render_pki_entities() so that entities.json
    exists before site-pki.yml runs (site-pki.yml loads it in a pre_tasks block).
    This covers the _ensure_prereqs path (provision / obs_up) which builds the step
    without going through the explicit pki ensure / pki issue commands. (#351)
    """
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    original_render = cli._render_pki_entities
    calls: list[bool] = []

    def _spy_render() -> Path:
        calls.append(True)
        return original_render()

    monkeypatch.setattr(cli, "_render_pki_entities", _spy_render)

    cli._pki_ensure_step()

    assert calls, "_render_pki_entities was NOT called by _pki_ensure_step()"
