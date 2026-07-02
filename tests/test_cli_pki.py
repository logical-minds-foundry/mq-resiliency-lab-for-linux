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

# The app-org QM CNs derived from each stack's #351 short token (<short>APP), and
# the single shared svc-org counterparty CN (SVCQM) every stack talks to (#446).
_APP_CNS = {"PCMKAPP", "RDQMAPP", "NHARAPP"}
_SVC_CNS = {"SVCQM"}

# The full entity set the stack model produces for this topology.
_STATIC_CNS = _APP_CNS | _SVC_CNS | _FIXED_CNS

# Minimal topology covering the three provisioned stacks; QM CNs derive from short.
_PKI_TOPO = (
    "nodes: {}\n"
    "groups: {}\n"
    "stacks:\n"
    "  pcmk-ubuntu:\n"
    "    mechanism: pacemaker-san\n    os: ubuntu\n    short: PCMK\n"
    "    groups: []\n    qm: {}\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n    os: rhel\n    short: RDQM\n"
    "    groups: []\n    qm: {}\n"
    "  nativeha-rhel:\n"
    "    mechanism: native-ha\n    os: rhel\n    short: NHAR\n"
    "    groups: []\n    qm: {}\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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


def _seed(monkeypatch, tmp_path, topo="nodes: {}\ngroups: {}\nstacks: {}\n"):
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
    result = CliRunner().invoke(cli.app, ["pki", "issue", "PCMKAPP"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible-playbook",
        "site-pki.yml",
        "-c",
        "local",
        "-i",
        "localhost,",
        "-e",
        "pki_only=PCMKAPP",
    ]
    # Entities file must have been rendered before the playbook ran.
    assert (tmp_path / "build" / "work" / "pki" / "entities.json").exists()


def test_pki_list_prints_entities_from_topology(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    result = CliRunner().invoke(cli.app, ["pki", "list"])
    assert result.exit_code == 0
    assert "PCMKAPP" in result.output
    assert "app-org" in result.output


# --- _render_pki_entities ---------------------------------------------------


def test_render_pki_entities_includes_derived_qm_cns(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    data = json.loads(path.read_text())
    cns = {e["cn"]: e["org"] for e in data}
    assert cns["PCMKAPP"] == "app-org"
    assert cns["SVCQM"] == "svc-org"  # the single shared counterparty (#446)


def test_render_pki_entities_includes_all_qm_cns(monkeypatch, tmp_path):
    """Every provisioned stack's app-org and svc-org QM CN must appear."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    data = json.loads(path.read_text())
    cns = {e["cn"] for e in data}
    assert (_APP_CNS | _SVC_CNS).issubset(cns)


def test_render_pki_entities_preserves_fixed_non_qm_entities(monkeypatch, tmp_path):
    """Fixed non-QM entity CNs must be present verbatim."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    assert _FIXED_CNS.issubset(cns)


def test_render_pki_entities_full_set_matches_static_list(monkeypatch, tmp_path):
    """The rendered CN set must equal the stack-derived entity set exactly."""
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
    for qm_cn in _APP_CNS:
        e = by_cn[qm_cn]
        assert e["org"] == "app-org"
        assert e["ou"] == "messaging"
        assert e["kind"] == "personal"
        assert e["trust"] == ["svc-org"]

    # svc-org QMs
    for svc_cn in _SVC_CNS:
        e = by_cn[svc_cn]
        assert e["org"] == "svc-org"
        assert e["ou"] == "messaging"
        assert e["kind"] == "personal"
        assert e["trust"] == ["app-org"]


def test_render_pki_entities_dedupes_repeated_svc_cn(monkeypatch, tmp_path):
    """The shared svc CN emits only one svc entity across stacks (dedupe) — and the
    app CN dedupes when two stacks share a short (#446)."""
    topo = (
        "nodes: {}\ngroups: {}\n"
        "stacks:\n"
        "  a:\n    mechanism: m\n    os: o\n    short: PCMK\n    groups: []\n    qm: {}\n"
        "  b:\n    mechanism: m\n    os: o\n    short: PCMK\n    groups: []\n    qm: {}\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    )
    _seed(monkeypatch, tmp_path, topo)
    data = json.loads(cli._render_pki_entities().read_text())
    assert sum(1 for e in data if e["cn"] == "PCMKAPP") == 1
    assert sum(1 for e in data if e["cn"] == "SVCQM") == 1


def test_render_pki_entities_exactly_one_svc_org_cn_is_svcqm(monkeypatch, tmp_path):
    """The svc side collapses to a single shared SVCQM cert across all stacks (#446) —
    not one {short}SVC per stack. Locks the dedup the shared model relies on."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    data = json.loads(cli._render_pki_entities().read_text())
    # the QM-derived svc-org CN (excluding the fixed svc-responder client cert)
    svc_qm_cns = [e["cn"] for e in data if e.get("org") == "svc-org" and e["cn"] not in _FIXED_CNS]
    assert svc_qm_cns == ["SVCQM"]


def test_render_pki_entities_writes_to_work_pki(monkeypatch, tmp_path):
    """The rendered file must land at build/work/pki/entities.json."""
    _seed(monkeypatch, tmp_path, _PKI_TOPO)
    path = cli._render_pki_entities()
    assert path == tmp_path / "build" / "work" / "pki" / "entities.json"


def test_render_pki_entities_no_stacks_yields_only_fixed(monkeypatch, tmp_path):
    """When there are no stacks, only the fixed non-QM entities are emitted."""
    _seed(monkeypatch, tmp_path)  # empty topology — no stacks
    data = json.loads(cli._render_pki_entities().read_text())
    cns = {e["cn"] for e in data}
    assert cns == _FIXED_CNS


def test_render_pki_entities_stack_without_short_is_skipped(monkeypatch, tmp_path):
    """A reserved stack with no short token contributes no QM entities."""
    topo = (
        "nodes: {}\ngroups: {}\n"
        "stacks:\n"
        "  reserved:\n    mechanism: m\n    os: o\n    short: ''\n    groups: []\n    qm: {}\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    )
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
