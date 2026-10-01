"""Huge-page reservation/release wiring in bootstrap, commons up and teardown (#1241).

With the macos `memory_backing: hugepages` lever set, the bootstrap reserves the stack's
guest pages before the vms phase's first `vagrant up` (a preflight step + perf note), a
short reservation fails the run loud, and the last stack's teardown releases them. With
the lever unset nothing changes: no play, no extra virsh probe.
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, hugepages
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests import test_cli_bootstrap as boot
from tests import test_cli_teardown as tear
from tests.fakes import RecordingRunner

if TYPE_CHECKING:
    from collections.abc import Callable

    from mqlab.runner import Command

LEVER = "memory_backing: hugepages\n"
# 9 guests in the bootstrap fixture topology, none with memory -> 1024 MiB = 512 pages each.
NEEDED_ALL = 9 * 512 + hugepages.MARGIN_PAGES
MEMINFO = (
    "HugePages_Total:  {total}\nHugePages_Free:  {total}\nHugePages_Rsvd:  0\n"
    "Hugepagesize:  2048 kB\n"
)
RECLAIM_LINES = [
    f"TASK [{hugepages.RECLAIM_TASK}] *********",
    "Thursday 01 October 2026  11:15:13 -0400 (0:00:00.043)       0:00:00.082 ***** ",
    "changed: [localhost]",
    "TASK [Re-read the huge-page counters after the retry] *****",
    "Thursday 01 October 2026  11:15:15 -0400 (0:00:02.000)       0:00:02.082 ***** ",
]


class _ArgvRunner(RecordingRunner):
    """Exit 0 and no output for every command, except those `script` matches by argv
    token: (exit code, lines). On the reservation play it also bumps the fake meminfo."""

    def __init__(
        self,
        script: dict[str, tuple[int, list[str]]] | None = None,
        on_reserve: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.script = script or {}
        self.on_reserve = on_reserve

    def run(self, command: Command, on_line: Callable[[str], None]) -> int:
        self.recorded.append(command)
        if self.on_reserve is not None and hugepages.PLAYBOOK in command.argv:
            self.on_reserve()
        for token, (code, lines) in self.script.items():
            if token in command.argv:
                for line in lines:
                    on_line(line)
                return code
        return 0


def _meminfo(monkeypatch, tmp_path, total: int = 0):
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO.format(total=total))
    monkeypatch.setattr(hugepages, "MEMINFO", path)
    return path


def _perf_deps(runner):
    buf = io.StringIO()
    deps = cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=buf, force_terminal=False, width=400)),
        transcript=Transcript(transcript_path("bootstrap", "20260627T000000Z")),
        pauser=boot._NoPause(),
    )
    return deps, buf


def _bootstrap(monkeypatch, tmp_path, runner, *args, topo=boot.TOPO + LEVER, states=None):
    boot._seed(monkeypatch, tmp_path, topo)
    st = states if states is not None else boot._states(net=False, vms=False)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: st)
    deps, buf = _perf_deps(runner)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: deps)
    boot._stub_ensure(monkeypatch)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", *args])
    return result, buf


def _report(tmp_path):
    (path,) = sorted((tmp_path / "build" / "state" / "runs").glob("perf-*.json"))
    return json.loads(path.read_text())


def _argv_index(runner, pred) -> int:
    return next(i for i, c in enumerate(runner.recorded) if pred(c.argv))


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def test_bootstrap_reserves_before_any_vagrant_up_and_reports_it(monkeypatch, tmp_path):
    path = _meminfo(monkeypatch, tmp_path)
    runner = _ArgvRunner(on_reserve=lambda: path.write_text(MEMINFO.format(total=NEEDED_ALL)))
    result, buf = _bootstrap(monkeypatch, tmp_path, runner)
    assert result.exit_code == 0, result.output
    reserve = _argv_index(runner, lambda a: hugepages.PLAYBOOK in a)
    first_up = _argv_index(runner, lambda a: a[:2] == ["vagrant", "up"])
    assert reserve < first_up
    assert runner.recorded[reserve].argv[-1] == f"hugepages_needed={NEEDED_ALL}"
    report = _report(tmp_path)
    labels = [s["label"] for s in report["phases"]["preflight"]["steps"]]
    assert labels[-1] == f"reserve huge pages ({NEEDED_ALL} x 2 MiB)"
    (note,) = [n for n in report["notes"] if n.startswith("huge pages:")]
    assert f"needed {NEEDED_ALL} x 2 MiB" in note
    assert "; reserved; " in note
    assert "HugePages_Total=0" in note
    assert f"after HugePages_Total={NEEDED_ALL}" in note
    assert "huge pages: needed" in buf.getvalue()


def test_bootstrap_sizes_only_guests_not_yet_running(monkeypatch, tmp_path):
    _meminfo(monkeypatch, tmp_path)
    states = boot._states(net=True, vms=False)
    states["domains"] = {"lab_obs": "running", "lab_san-a": "shut off"}
    runner = _ArgvRunner()
    result, _ = _bootstrap(monkeypatch, tmp_path, runner, "--no-dr", states=states)
    assert result.exit_code == 0, result.output
    reserve = runner.recorded[_argv_index(runner, lambda a: hugepages.PLAYBOOK in a)]
    # --no-dr: pcmk-b1 is not booted; obs is live (holds its pages); san-a is shut off.
    assert reserve.argv[-1] == f"hugepages_needed={7 * 512 + hugepages.MARGIN_PAGES}"


def test_bootstrap_notes_a_reclaim_retry(monkeypatch, tmp_path):
    _meminfo(monkeypatch, tmp_path)
    runner = _ArgvRunner(script={hugepages.PLAYBOOK: (0, RECLAIM_LINES)})
    result, _ = _bootstrap(monkeypatch, tmp_path, runner)
    assert result.exit_code == 0, result.output
    (note,) = [n for n in _report(tmp_path)["notes"] if n.startswith("huge pages:")]
    assert "reserved after a drop_caches + compact_memory retry" in note


def test_a_short_reservation_fails_the_bootstrap_before_any_boot(monkeypatch, tmp_path):
    _meminfo(monkeypatch, tmp_path)
    runner = _ArgvRunner(script={hugepages.PLAYBOOK: (2, ["huge-page reservation SHORT"])})
    result, buf = _bootstrap(monkeypatch, tmp_path, runner)
    assert result.exit_code == 2
    assert not any(c.argv[:2] == ["vagrant", "up"] for c in runner.recorded)
    assert "huge-page reservation failed (needed vs got above)" in result.output
    assert "huge-page reservation SHORT" in buf.getvalue()
    report = _report(tmp_path)
    assert "bootstrap FAILED in phase preflight (exit 2)" in report["notes"]
    assert [s["ok"] for s in report["phases"]["preflight"]["steps"]][-1] is False


def test_all_guests_running_needs_no_reservation(monkeypatch, tmp_path):
    _meminfo(monkeypatch, tmp_path)
    runner = _ArgvRunner()
    states = boot._states(net=True, vms=True)
    result, _ = _bootstrap(monkeypatch, tmp_path, runner, "--only", "vms", states=states)
    assert result.exit_code == 0, result.output
    assert not any(hugepages.PLAYBOOK in c.argv for c in runner.recorded)
    notes = _report(tmp_path)["notes"]
    assert "huge pages: every guest is already running — no reservation needed" in notes


def test_no_reservation_when_the_vms_phase_does_not_run(monkeypatch, tmp_path):
    runner = _ArgvRunner()
    result, _ = _bootstrap(monkeypatch, tmp_path, runner, "--only", "observe")
    assert result.exit_code == 0, result.output
    assert not any(hugepages.PLAYBOOK in c.argv for c in runner.recorded)


def test_no_reservation_without_the_lever(monkeypatch, tmp_path):
    runner = _ArgvRunner()
    result, _ = _bootstrap(monkeypatch, tmp_path, runner, topo=boot.TOPO)
    assert result.exit_code == 0, result.output
    assert not any(hugepages.PLAYBOOK in c.argv for c in runner.recorded)
    assert not any(n.startswith("huge pages") for n in _report(tmp_path)["notes"])


# --------------------------------------------------------------------------- #
# commons up
# --------------------------------------------------------------------------- #
def _commons_up(monkeypatch, tmp_path, runner, topo):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)
    monkeypatch.setattr(cli, "_ensure_prereqs_for_commons", lambda **k: None)
    deps, _ = _perf_deps(runner)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: deps)
    return CliRunner().invoke(cli.app, ["commons", "up"])


def test_commons_up_reserves_for_commons_not_running(monkeypatch, tmp_path):
    from tests import test_cli_commons as com

    _meminfo(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_states", lambda deps: {"lab_obs": "running"})
    runner = _ArgvRunner()
    result = _commons_up(monkeypatch, tmp_path, runner, com.TOPO + LEVER)
    assert result.exit_code == 0, result.output
    reserve = _argv_index(runner, lambda a: hugepages.PLAYBOOK in a)
    assert reserve < _argv_index(runner, lambda a: a[:2] == ["vagrant", "up"])
    guests = [m for m in cli._commons_members() if m != "obs"]
    needed = hugepages.needed_pages(cli.topology.load(), guests)
    assert runner.recorded[reserve].argv[-1] == f"hugepages_needed={needed}"


def test_commons_up_short_reservation_fails_loud(monkeypatch, tmp_path):
    from tests import test_cli_commons as com

    _meminfo(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_states", lambda deps: {})
    runner = _ArgvRunner(script={hugepages.PLAYBOOK: (2, [])})
    result = _commons_up(monkeypatch, tmp_path, runner, com.TOPO + LEVER)
    assert result.exit_code == 2
    assert not any(c.argv[:2] == ["vagrant", "up"] for c in runner.recorded)


def test_commons_up_with_every_commons_running_needs_no_reservation(monkeypatch, tmp_path):
    from tests import test_cli_commons as com

    running = {f"lab_{m}": "running" for m in ("obs", "mon-probe", "svc-sim", "app-client")}
    monkeypatch.setattr(cli, "_probe_states", lambda deps: running)
    runner = _ArgvRunner()
    result = _commons_up(monkeypatch, tmp_path, runner, com.TOPO + LEVER)
    assert result.exit_code == 0, result.output
    assert not any(hugepages.PLAYBOOK in c.argv for c in runner.recorded)


def test_commons_up_without_lever_never_probes(monkeypatch, tmp_path):
    from tests import test_cli_commons as com

    def no_probe(deps):
        pytest.fail("commons up must not probe virsh when the lever is unset")

    monkeypatch.setattr(cli, "_probe_states", no_probe)
    runner = _ArgvRunner()
    result = _commons_up(monkeypatch, tmp_path, runner, com.TOPO)
    assert result.exit_code == 0, result.output
    assert not any(hugepages.PLAYBOOK in c.argv for c in runner.recorded)


# --------------------------------------------------------------------------- #
# teardown: release only when the last stack is down
# --------------------------------------------------------------------------- #
def _teardown(monkeypatch, tmp_path, *, other_up, commons=False, topo=tear.TOPO + LEVER):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "networks").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)
    tear._stub_probe_states(monkeypatch)
    calls: list[str] = []

    def other_stacks_up(deps, exclude):
        calls.append(exclude)
        return other_up

    monkeypatch.setattr(cli, "_other_stacks_up", other_stacks_up)
    monkeypatch.setattr(cli.box, "gc_orphaned_images_best_effort", lambda: None)
    runner = _ArgvRunner()
    deps, buf = _perf_deps(runner)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: deps)
    args = ["teardown", "pcmk-ubuntu", *(["--commons"] if commons else [])]
    result = CliRunner().invoke(cli.app, args)
    return result, runner, buf, calls


def _released(runner) -> bool:
    return any("hugepages_release=true" in c.argv for c in runner.recorded)


def test_last_stack_down_releases_after_every_destroy(monkeypatch, tmp_path):
    result, runner, _, _ = _teardown(monkeypatch, tmp_path, other_up=False)
    assert result.exit_code == 0, result.output
    assert _released(runner)
    assert hugepages.PLAYBOOK in runner.recorded[-1].argv  # after every destroy step


def test_other_stack_up_keeps_the_pages(monkeypatch, tmp_path):
    result, runner, buf, _ = _teardown(monkeypatch, tmp_path, other_up=True)
    assert result.exit_code == 0, result.output
    assert not _released(runner)
    assert "huge pages kept — another stack's guests still use them" in buf.getvalue()


def test_forced_commons_teardown_still_keeps_pages_under_running_guests(monkeypatch, tmp_path):
    result, runner, _, calls = _teardown(monkeypatch, tmp_path, other_up=True, commons=True)
    assert result.exit_code == 0, result.output
    assert calls == ["pcmk-ubuntu"]  # the reference count is probed for the release
    assert not _released(runner)


def test_forced_commons_teardown_without_lever_skips_the_reference_count(monkeypatch, tmp_path):
    result, runner, _, calls = _teardown(
        monkeypatch, tmp_path, other_up=False, commons=True, topo=tear.TOPO
    )
    assert result.exit_code == 0, result.output
    assert calls == []  # unchanged pre-#1241 behaviour
    assert not _released(runner)


def test_no_release_without_lever(monkeypatch, tmp_path):
    result, runner, buf, _ = _teardown(monkeypatch, tmp_path, other_up=False, topo=tear.TOPO)
    assert result.exit_code == 0, result.output
    assert not _released(runner)
    assert "huge pages" not in buf.getvalue()
