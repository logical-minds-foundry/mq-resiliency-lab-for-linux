from __future__ import annotations

import subprocess

from mqlab import runner as runner_mod
from mqlab.runner import Command, SubprocessRunner


def test_command_display_is_verbatim_argv():
    cmd = Command(["virsh", "net-start", "net-wan"])
    assert cmd.display() == "virsh net-start net-wan"


def test_subprocess_runner_streams_lines_and_returns_exit_code():
    lines: list[str] = []
    runner = SubprocessRunner()
    code = runner.run(Command(["sh", "-c", "echo one; echo two; exit 3"]), lines.append)
    assert lines == ["one", "two"]
    assert code == 3


def test_subprocess_runner_merges_stderr_into_the_stream():
    lines: list[str] = []
    runner = SubprocessRunner()
    code = runner.run(Command(["sh", "-c", "echo out; echo err 1>&2"]), lines.append)
    assert set(lines) == {"out", "err"}
    assert code == 0


def test_subprocess_runner_handles_no_output():
    lines: list[str] = []
    runner = SubprocessRunner()
    code = runner.run(Command(["sh", "-c", "exit 0"]), lines.append)
    assert lines == []
    assert code == 0


def test_subprocess_runner_passes_command_env():
    lines: list[str] = []
    SubprocessRunner().run(
        Command(["sh", "-c", "echo $MQLAB_TEST_VAR"], env={"MQLAB_TEST_VAR": "xyz"}),
        lines.append,
    )
    assert lines == ["xyz"]


def test_subprocess_runner_merges_command_env_over_os_environ(monkeypatch):
    monkeypatch.setenv("MQLAB_BASE", "base")
    lines: list[str] = []
    SubprocessRunner().run(
        Command(["sh", "-c", "echo $MQLAB_BASE $MQLAB_EXTRA"], env={"MQLAB_EXTRA": "extra"}),
        lines.append,
    )
    assert lines == ["base extra"]  # inherited PATH/etc preserved, command.env added


def test_subprocess_runner_gives_each_child_its_own_stdin(monkeypatch):
    # #1420: a step must never inherit mqlab's fd 0. O_NONBLOCK lives on the shared open
    # file description, so a concurrent ssh flipping it made ansible-playbook refuse.
    seen: dict[str, object] = {}
    real_popen = subprocess.Popen

    def spy_popen(argv, **kwargs):  # noqa: ANN001, ANN003, ANN202 - test spy
        seen.update(kwargs)
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", spy_popen)
    lines: list[str] = []
    # `cat` on its own /dev/null hits EOF at once: no output, exit 0, never a hang.
    code = SubprocessRunner().run(Command(["sh", "-c", "cat; echo done"]), lines.append)
    assert seen["stdin"] is subprocess.DEVNULL
    assert lines == ["done"]
    assert code == 0
