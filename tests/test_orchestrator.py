from __future__ import annotations

import io

import pytest
from rich.console import Console

from mqlab.orchestrator import CommandStep, StepFailedError, run_steps
from mqlab.render import Renderer
from mqlab.runner import Command
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class SpyPauser:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


def _renderer() -> Renderer:
    return Renderer(Console(file=io.StringIO(), force_terminal=False, width=80))


def _transcript(tmp_path, monkeypatch) -> Transcript:
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    return Transcript(transcript_path("test", "20260609T000000Z"))


def test_run_steps_runs_each_step_and_tees_to_transcript(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult(["Network net-wan started"])])
    transcript = _transcript(tmp_path, monkeypatch)
    clock = iter([10.0, 10.5])
    run_steps(
        [CommandStep("networks up", Command(["bash", "net-up.sh"]))],
        runner=runner,
        renderer=_renderer(),
        transcript=transcript,
        step_mode=False,
        pauser=SpyPauser(),
        now=lambda: next(clock),
    )
    transcript.close()
    body = transcript.path.read_text(encoding="utf-8")
    assert "$ bash net-up.sh" in body
    assert "Network net-wan started" in body
    assert "OK: networks up 0.50s" in body
    assert "SUMMARY: 1/1 steps 0.50s" in body
    assert runner.recorded[0].argv == ["bash", "net-up.sh"]


def test_run_steps_raises_on_nonzero_exit(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=3)])
    transcript = _transcript(tmp_path, monkeypatch)
    with pytest.raises(StepFailedError) as caught:
        run_steps(
            [CommandStep("networks up", Command(["bash", "net-up.sh"]))],
            runner=runner,
            renderer=_renderer(),
            transcript=transcript,
            step_mode=False,
            pauser=SpyPauser(),
            now=lambda: 0.0,
        )
    assert caught.value.exit_code == 3


def test_step_mode_pauses_between_steps_but_not_after_the_last(tmp_path, monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    transcript = _transcript(tmp_path, monkeypatch)
    pauser = SpyPauser()
    run_steps(
        [
            CommandStep("a", Command(["true"])),
            CommandStep("b", Command(["true"])),
        ],
        runner=runner,
        renderer=_renderer(),
        transcript=transcript,
        step_mode=True,
        pauser=pauser,
        now=lambda: 0.0,
    )
    assert pauser.calls == 1


def test_run_steps_with_no_steps_emits_zero_summary(tmp_path, monkeypatch):
    transcript = _transcript(tmp_path, monkeypatch)
    run_steps(
        [],
        runner=RecordingRunner(),
        renderer=_renderer(),
        transcript=transcript,
        step_mode=False,
        pauser=SpyPauser(),
        now=lambda: 0.0,
    )
    transcript.close()
    assert "SUMMARY: 0/0 steps 0.00s" in transcript.path.read_text(encoding="utf-8")
