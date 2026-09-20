from __future__ import annotations

import io

import pytest
from rich.console import Console

from mqlab.orchestrator import CommandStep, RetryPolicy, StepFailedError, run_steps
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


# --------------------------------------------------------------------------- #
# RetryPolicy + bounded boot-retry (#1164)
# --------------------------------------------------------------------------- #
class RecordingSleep:
    """Records the delays run_steps would have slept for (no real sleeping in tests)."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempts": 0},
        {"attempts": -1},
        {"base_delay": -1.0},
        {"backoff": -2.0},
        {"max_delay": -5.0},
    ],
)
def test_retry_policy_rejects_garbled_values(kwargs):
    """A garbled policy fails loud (no silent clamp) — the _boot_batch stance (#1164)."""
    with pytest.raises(ValueError, match="RetryPolicy"):
        RetryPolicy(**kwargs)


def test_retry_policy_delay_before_backs_off_and_caps():
    policy = RetryPolicy(attempts=5, base_delay=5.0, backoff=2.0, max_delay=30.0)
    assert policy.delay_before(1) == 5.0
    assert policy.delay_before(2) == 10.0
    assert policy.delay_before(3) == 20.0
    # 5 * 2**3 = 40, capped at max_delay=30.
    assert policy.delay_before(4) == 30.0


def test_run_steps_retries_transient_failure_then_succeeds(tmp_path, monkeypatch):
    """A retryable step whose first `vagrant up` exits non-zero is re-run (after a
    backoff sleep) and succeeds — the run does NOT abort (#1164)."""
    runner = RecordingRunner(
        results=[
            ScriptedResult(["Fog::Errors::TimeoutError"], exit_code=1),
            ScriptedResult(["Machine booted and ready!"], exit_code=0),
        ]
    )
    transcript = _transcript(tmp_path, monkeypatch)
    sleeper = RecordingSleep()
    policy = RetryPolicy(attempts=3, base_delay=15.0, backoff=2.0, max_delay=60.0)
    run_steps(
        [CommandStep("stack vms up", Command(["vagrant", "up", "infra-client"]), retry=policy)],
        runner=runner,
        renderer=_renderer(),
        transcript=transcript,
        step_mode=False,
        pauser=SpyPauser(),
        now=lambda: 0.0,
        sleep=sleeper,
    )
    transcript.close()
    body = transcript.path.read_text(encoding="utf-8")
    assert len(runner.recorded) == 2  # first attempt + one retry
    assert sleeper.delays == [15.0]  # one backoff before the retry
    assert "RETRY: stack vms up: attempt 1/3 failed (exit 1); retrying in 15s" in body
    assert "OK: stack vms up" in body
    assert "SUMMARY: 1/1 steps" in body


def test_run_steps_retry_exhausted_fails_loud(tmp_path, monkeypatch):
    """After the bounded cap of attempts the run halts loud — retry never masks a
    persistent boot failure (spec §8)."""
    runner = RecordingRunner(
        results=[
            ScriptedResult(["boom"], exit_code=2),
            ScriptedResult(["boom"], exit_code=2),
        ]
    )
    transcript = _transcript(tmp_path, monkeypatch)
    sleeper = RecordingSleep()
    policy = RetryPolicy(attempts=2, base_delay=1.0, backoff=1.0, max_delay=1.0)
    with pytest.raises(StepFailedError) as caught:
        run_steps(
            [CommandStep("stack vms up", Command(["vagrant", "up", "san-a"]), retry=policy)],
            runner=runner,
            renderer=_renderer(),
            transcript=transcript,
            step_mode=False,
            pauser=SpyPauser(),
            now=lambda: 0.0,
            sleep=sleeper,
        )
    assert caught.value.exit_code == 2
    assert len(runner.recorded) == 2  # both allowed attempts spent
    assert sleeper.delays == [1.0]  # one backoff between the two attempts
