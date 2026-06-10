from __future__ import annotations

import io

from rich.console import Console

from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from mqlab.vmstatus import vm_status_core
from tests.fakes import RecordingRunner, ScriptedResult

SAMPLE = """\
 Id   Name             State
---------------------------------
 -    lab_pcmk-a1      shut off
 49   lab_pcmk-b1      running
"""


def test_vm_status_core_renders_full_fleet_and_tees(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "defaults: { platform: ubuntu2404-arm64 }\nnodes:\n"
        "  rdqm-a1: { platform: rhel96-x86_64 }\n  pcmk-a1: {}\n  pcmk-b1: {}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "setups:\n  rdqm-ha:\n    groups: [rdqm_a]\n"
        "  pcmk-san:\n    groups: [pcmk_a, pcmk_b]\n"
    )
    buffer = io.StringIO()
    renderer = Renderer(Console(file=buffer, force_terminal=False, width=120))
    transcript = Transcript(transcript_path("vm-status", "20260609T000000Z"))
    runner = RecordingRunner(results=[ScriptedResult(SAMPLE.splitlines())])
    code = vm_status_core(runner, renderer, transcript)
    transcript.close()
    assert code == 0
    out = buffer.getvalue()
    assert "$ virsh -c qemu:///system list --all" in out  # command echoed (transparency)
    assert "lab_pcmk-a1" in out  # raw virsh output streamed to screen — the State source (#88)
    assert "topology.yaml" in out  # the other source named on the table (#88)
    assert "rdqm-a1" in out  # full fleet — incl the defined-but-not-instantiated RHEL node
    assert "not created" in out
    assert "rhel96-x86_64" in out
    assert "rdqm-ha" in out  # config-driven Setup(s) column, from topology.yaml setups (#90)
    body = transcript.path.read_text(encoding="utf-8")
    assert "lab_pcmk-b1      running" in body  # raw rows teed to the transcript


def test_vm_status_core_filters_to_given_guests(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  rdqm-a1: {}\n  pcmk-a1: {}\n  pcmk-b1: {}\n"
    )
    buffer = io.StringIO()
    renderer = Renderer(Console(file=buffer, force_terminal=False, width=120))
    transcript = Transcript(transcript_path("vm-status", "20260610T000000Z"))
    runner = RecordingRunner(results=[ScriptedResult([])])  # empty virsh output
    code = vm_status_core(runner, renderer, transcript, guests=["pcmk-a1", "pcmk-b1"])
    transcript.close()
    assert code == 0
    out = buffer.getvalue()
    assert "pcmk-a1" in out
    assert "pcmk-b1" in out
    assert "rdqm-a1" not in out  # filtered out of the table
