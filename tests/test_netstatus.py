from __future__ import annotations

import io

from rich.console import Console

from mqlab.netstatus import NetRow, net_status_core, parse_net_list
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

SAMPLE = """\
 Name              State    Autostart   Persistent
--------------------------------------------------------
 default           active   yes         yes
 net-wan           active   yes         yes
 net-san-b         inactive no          yes
"""


def test_parse_net_list_extracts_rows_and_skips_chrome():
    rows = parse_net_list(SAMPLE)
    assert rows == [
        NetRow("default", "active", "yes", "yes"),
        NetRow("net-wan", "active", "yes", "yes"),
        NetRow("net-san-b", "inactive", "no", "yes"),
    ]


def test_parse_net_list_ignores_blank_and_short_lines():
    assert parse_net_list("\n   \nName x\n----\n bad row\n") == []


def test_net_status_core_streams_raw_output_and_tees(tmp_path, monkeypatch):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    buffer = io.StringIO()
    renderer = Renderer(Console(file=buffer, force_terminal=False, width=100))
    transcript = Transcript(transcript_path("net-status", "20260609T000000Z"))
    runner = RecordingRunner(results=[ScriptedResult(SAMPLE.splitlines())])
    code = net_status_core(runner, renderer, transcript)
    transcript.close()
    assert code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "net-list", "--all"]
    out = buffer.getvalue()
    assert "$ virsh -c qemu:///system net-list --all" in out
    assert "net-wan" in out  # raw line + table both render
    body = transcript.path.read_text(encoding="utf-8")
    assert "$ virsh -c qemu:///system net-list --all" in body
    assert "net-san-b" in body  # raw virsh lines teed to the transcript
