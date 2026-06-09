from __future__ import annotations

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
