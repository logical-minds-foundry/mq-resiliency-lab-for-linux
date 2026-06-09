from __future__ import annotations

import io

from rich.console import Console

from mqlab.render import Renderer, command_line, fail_line, ok_line, output_line, summary_line


def test_command_line_is_prefixed_and_verbatim():
    assert command_line("virsh net-start net-wan").plain == "  $ virsh net-start net-wan"


def test_output_line_is_indented():
    assert output_line("Network net-wan started").plain == "      Network net-wan started"


def test_ok_line_shows_label_and_elapsed():
    assert ok_line("networks up", 0.42).plain == "  ✓ networks up   0.42s"


def test_fail_line_shows_exit_code():
    assert fail_line("networks up", 3).plain == "  ✗ networks up   exit 3"


def test_summary_line_reports_steps_and_total():
    assert summary_line(1, 1, 3.10).plain == "  ✓ 1/1 steps   3.10s"


def test_renderer_prints_through_a_console():
    buffer = io.StringIO()
    renderer = Renderer(Console(file=buffer, force_terminal=False, width=80))
    renderer.command("virsh net-start net-wan")
    renderer.output("Network net-wan started")
    renderer.ok("networks up", 0.42)
    renderer.fail("networks up", 3)
    renderer.error("--step requires a terminal")
    renderer.summary(1, 1, 3.10)
    text = buffer.getvalue()
    assert "$ virsh net-start net-wan" in text
    assert "Network net-wan started" in text
    assert "networks up" in text
    assert "exit 3" in text
    assert "--step requires a terminal" in text
    assert "1/1 steps" in text
