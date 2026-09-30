"""Tests for mqlab.perfrun — perf capture wired into bootstrap (#1205).

No live calls: the sampler runs over the conftest `fake_perf_source`, transcripts are
tmp files of canned lines (the real ansible `profile_tasks` format, copied from a lab
bootstrap transcript), and the renderer writes to a StringIO console.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from mqlab import perfrun, phases
from mqlab.orchestrator import CommandStep
from mqlab.perf import PerfRecord
from mqlab.perfrun import (
    BOOT_ATTRIBUTION,
    OPENSEARCH_BOUND_GAP,
    READINESS_TASKS,
    BootstrapPerf,
    ansible_task_outcomes,
    report_path,
    vagrant_up_batches,
)
from mqlab.perfrun import default_source as _real_default_source  # before the autouse stub
from mqlab.perfsampler import RealSource
from mqlab.render import Renderer
from mqlab.runner import Command

if TYPE_CHECKING:
    from mqlab.perfsampler import Sampler

REPO = Path(__file__).resolve().parents[1]

OS_TASK = READINESS_TASKS["opensearch_green"]
DP_TASK = READINESS_TASKS["data_prepper_ready"]
DB_TASK = READINESS_TASKS["dashboards_ready"]


def _timing(prev: str, total: str) -> str:
    """One profile_tasks timing line, as ansible prints it (trailing space included)."""
    return f"Wednesday 23 September 2026  08:21:34 -0400 ({prev})       {total} ***** "


# A successful observe-phase slice: OpenSearch green after 20:41.136, Data Prepper after
# 8.440 s, Dashboards after 15.003 s (closed by the TASKS RECAP timing line).
OBSERVE_OK = [
    "TASK [opensearch : enable + start opensearch] ***********************************",
    _timing("0:00:01.000", "0:00:53.000"),
    "changed: [obs]",
    f"TASK [{OS_TASK}] *********",
    _timing("0:00:15.853", "0:00:54.556"),
    f"FAILED - RETRYING: [obs]: {OS_TASK} (479 retries left).",
    "ok: [obs]",
    "TASK [opensearch : PUT the logs-* index template (number_of_replicas:0)] ***",
    _timing("0:20:41.136", "0:21:35.693"),
    "ok: [obs]",
    f"TASK [{DP_TASK}] ***",
    _timing("0:00:08.440", "0:22:14.016"),
    "ok: [obs]",
    "RUNNING HANDLER [data-prepper : restart] ***",
    _timing("0:00:08.440", "0:22:22.456"),
    f"TASK [{DB_TASK}] ***",
    _timing("0:00:15.003", "0:28:38.283"),
    "ok: [obs]",
    "PLAY RECAP *********************************************************************",
    "obs : ok=40 changed=3 unreachable=0 failed=0",
    "TASKS RECAP ********************************************************************",
    _timing("0:00:15.003", "0:28:53.286"),
]


def _renderer() -> tuple[Renderer, io.StringIO]:
    buf = io.StringIO()
    return Renderer(Console(file=buf, force_terminal=False, width=200)), buf


def _perf(renderer: Renderer | None = None) -> BootstrapPerf:
    return BootstrapPerf(PerfRecord("nativeha-ubuntu", 1000.0), renderer or _renderer()[0])


def _vms_step(label: str, *guests: str, serial: bool = False) -> CommandStep:
    flags = ["--no-parallel"] if serial else []
    return CommandStep(label, Command(["vagrant", "up", *flags, *guests]), phase="vms")


@pytest.fixture
def runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    return tmp_path / "build" / "state" / "runs"


def _report(runs: Path, ts: str = "20260930T000000Z") -> dict:
    return json.loads((runs / f"perf-{ts}.json").read_text())


# --------------------------------------------------------------------------- #
# pins: phase names + the ansible readiness task names
# --------------------------------------------------------------------------- #
def test_phase_literals_match_the_phase_registry():
    assert (perfrun._VMS, perfrun._OBSERVE) == (phases.VMS, phases.OBSERVE)
    assert {phases.VMS, phases.OBSERVE} <= {p.name for p in phases.PHASES}


@pytest.mark.parametrize("milestone", sorted(READINESS_TASKS))
def test_readiness_task_names_exist_in_their_roles(milestone):
    """Each milestone's task must exist verbatim in its role's configure tasks — a rename
    fails here, loudly, instead of silently dropping the milestone from every report."""
    role, _, name = READINESS_TASKS[milestone].partition(" : ")
    tasks = (REPO / "ansible" / "roles" / role / "tasks" / "configure.yml").read_text()
    assert re.search(rf"^- name: {re.escape(name)}$", tasks, re.MULTILINE), milestone


def test_report_path_is_under_the_runs_dir(runs):
    assert report_path("20260930T000000Z") == runs / "perf-20260930T000000Z.json"


def test_default_source_is_the_lab_source_over_the_topology():
    src = _real_default_source({"nodes": {"obs": {"nics": {"net-mgmt": "10.50.0.2"}}}})
    assert isinstance(src, RealSource)  # construction only — no probe is run
    assert src.addresses == {"obs": "10.50.0.2"}


# --------------------------------------------------------------------------- #
# ansible_task_outcomes — the profile_tasks parser
# --------------------------------------------------------------------------- #
def test_outcomes_time_each_wait_from_its_start_to_the_next_timing_line():
    out = ansible_task_outcomes(OBSERVE_OK, READINESS_TASKS.values())
    assert out[OS_TASK].seconds == pytest.approx(20 * 60 + 41.136)
    assert out[DP_TASK].seconds == pytest.approx(8.440)  # closed by a handler's timing
    assert out[DB_TASK].seconds == pytest.approx(15.003)  # closed by the TASKS RECAP
    assert all(o.ok and not o.failed for o in out.values())


def test_outcomes_record_a_fatal_and_an_unfinished_task_and_ignore_repeats():
    lines = [
        f"TASK [{OS_TASK}] ***",
        _timing("0:00:01.000", "0:00:01.000"),
        "fatal: [obs]: FAILED! => {}",
        "TASKS RECAP ***",
        _timing("0:40:00.000", "0:40:01.000"),
        f"TASK [{OS_TASK}] ***",  # a second run of the same task is ignored
        _timing("0:00:01.000", "0:40:02.000"),
        "ok: [obs]",
        f"TASK [{DP_TASK}] ***",
        _timing("0:00:01.000", "0:40:03.000"),
        "ok: [obs]",  # ...and the run ends mid-task: no closing timing line
    ]
    out = ansible_task_outcomes(lines, READINESS_TASKS.values())
    assert out[OS_TASK].failed and not out[OS_TASK].ok
    assert out[OS_TASK].seconds == pytest.approx(2400.0)
    assert out[DP_TASK].seconds is None
    assert DB_TASK not in out  # never started


def test_outcomes_ignore_timing_lines_with_no_banner_and_unnamed_tasks():
    lines = [_timing("0:00:01.000", "0:00:01.000"), "TASK [other] ***", _timing("0:0", "x")]
    assert ansible_task_outcomes(lines, READINESS_TASKS.values()) == {}


# --------------------------------------------------------------------------- #
# vagrant_up_batches
# --------------------------------------------------------------------------- #
def test_batches_map_each_vagrant_up_label_to_its_guests_dropping_flags():
    steps = [
        _vms_step("s vms up [1/2]", "san-a", "obs", serial=True),
        _vms_step("s vms up [2/2]", "app-client"),
        CommandStep("render", Command(["mqlab", "dns", "render"])),
    ]
    assert vagrant_up_batches(steps) == {
        "s vms up [1/2]": ["san-a", "obs"],
        "s vms up [2/2]": ["app-client"],
    }


# --------------------------------------------------------------------------- #
# BootstrapPerf.start — the sampler
# --------------------------------------------------------------------------- #
def test_start_samples_every_guest_immediately_with_the_record_as_t_origin(
    monkeypatch, tmp_path, fake_perf_source
):
    monkeypatch.setattr(perfrun.topology, "load", lambda: {})
    clock = iter([1000.0, 1004.0])
    perf = BootstrapPerf.start(
        "s", lambda: ["a", "b"], renderer=_renderer()[0], clock=lambda: next(clock)
    )
    assert perf._sampler is not None
    perf._sampler.stop()
    assert perf.record.started_at == 1000.0
    assert perf.record.samples[0].t == 4.0
    assert perf.record.samples[0].guest_steal == {"a": 2.5, "b": 2.5}
    assert sorted(fake_perf_source.probed) == ["a", "b"]


def test_start_notes_and_warns_when_the_sampler_cannot_start(monkeypatch):
    def boom() -> list[str]:
        raise LookupError("no such stack")

    monkeypatch.setattr(perfrun.topology, "load", lambda: {})
    renderer, buf = _renderer()
    perf = BootstrapPerf.start("s", boom, renderer=renderer)
    assert perf._sampler is None
    assert any("sampler not started" in n and "no such stack" in n for n in perf.record.notes)
    assert "perf: WARNING" in buf.getvalue()
    assert "bootstrap itself is unaffected" in buf.getvalue()


# --------------------------------------------------------------------------- #
# register_phase / outcome / finish
# --------------------------------------------------------------------------- #
def test_register_phase_degrades_on_a_malformed_step():
    renderer, buf = _renderer()
    perf = _perf(renderer)
    perf.register_phase("vms", cast("list[CommandStep]", [object()]))
    assert any("could not register the vms phase" in n for n in perf.record.notes)
    assert "perf: WARNING" in buf.getvalue()


def test_finish_attributes_each_batch_time_and_retries_to_every_vm_in_it(runs, tmp_path):
    perf = _perf()
    batch1 = _vms_step("s vms up [1/3]", "san-a", "pcmk-a1", serial=True)
    batch2 = _vms_step("s vms up [2/3]", "obs")
    batch3 = _vms_step("s vms up [3/3]", "app-client", "svc-sim")
    perf.register_phase("net", [])
    perf.register_phase("vms", [batch1, batch2, batch3])
    perf.record.step("net", "networks up", 1.0, 0)
    perf.record.step("vms", batch1.label, 154.95, 0)
    perf.record.step("vms", batch2.label, 310.0, 2)
    perf.failed("vms", 1)  # batch 3 never completed
    perf.finish(tmp_path / "absent.log", "20260930T000000Z")
    ms = _report(runs)["milestones"]
    assert ms["boot:san-a"] == ms["boot:pcmk-a1"] == 154.95
    assert ms["boot_retries:san-a"] == ms["boot_retries:pcmk-a1"] == 0
    assert ms["boot:obs"] == 310.0
    assert ms["boot_retries:obs"] == 2
    assert "boot:app-client" not in ms
    notes = _report(runs)["notes"]
    assert "bootstrap FAILED in phase vms (exit 1)" in notes
    assert BOOT_ATTRIBUTION in notes
    assert "boot: no timing for app-client, svc-sim (their vms batch did not complete)" in notes
    assert not any("readiness" in n or "opensearch" in n for n in notes)  # observe never ran


def test_finish_with_no_timed_batch_notes_only_the_gap(runs, tmp_path):
    perf = _perf()
    perf.register_phase("vms", [_vms_step("s vms up", "obs")])
    perf.finish(tmp_path / "absent.log", "20260930T000000Z")
    notes = _report(runs)["notes"]
    assert BOOT_ATTRIBUTION not in notes
    assert "boot: no timing for obs (their vms batch did not complete)" in notes
    assert "bootstrap did not complete (stopped outside a step; see transcript)" in notes


def test_finish_derives_readiness_milestones_from_the_transcript(runs, tmp_path):
    transcript = tmp_path / "t.log"
    transcript.write_text("\n".join(OBSERVE_OK) + "\n")
    renderer, buf = _renderer()
    perf = _perf(renderer)
    perf.register_phase("observe", [])
    perf.completed()
    perf.finish(transcript, "20260930T000000Z")
    report = _report(runs)
    assert report["milestones"] == {
        "opensearch_green": pytest.approx(1241.136),
        "data_prepper_ready": pytest.approx(8.44),
        "dashboards_ready": pytest.approx(15.003),
    }
    assert "opensearch_bound" not in report["milestones"]
    assert OPENSEARCH_BOUND_GAP in report["notes"]
    assert "bootstrap completed" in report["notes"]
    out = buf.getvalue()
    assert "Perf summary — nativeha-ubuntu" in out
    assert "opensearch_green" in out
    assert f"perf report -> {runs / 'perf-20260930T000000Z.json'}" in out


def test_finish_notes_each_readiness_wait_that_was_not_reached(runs, tmp_path):
    transcript = tmp_path / "t.log"
    lines = [
        f"TASK [{OS_TASK}] ***",
        _timing("0:00:01.000", "0:00:01.000"),
        "FAILED - RETRYING: [obs]: x (1 retries left).",
        "fatal: [obs]: FAILED! => {}",
        "TASKS RECAP ***",
        _timing("0:40:00.000", "0:40:01.000"),
        f"TASK [{DP_TASK}] ***",
        _timing("0:00:01.000", "0:40:02.000"),
    ]
    transcript.write_text("\n".join(lines) + "\n")
    perf = _perf()
    perf.register_phase("observe", [])
    perf.finish(transcript, "20260930T000000Z")
    report = _report(runs)
    assert report["milestones"] == {}
    notes = report["notes"]
    assert f"opensearch_green: not reached — {OS_TASK!r} did not succeed (2400.00s)" in notes
    assert f"data_prepper_ready: not observed — {DP_TASK!r} never finished" in notes
    assert f"dashboards_ready: not observed — task {DB_TASK!r} never started" in notes


def test_finish_degrades_every_perf_failure_to_a_warning_and_a_note(monkeypatch, runs, tmp_path):
    class BadSampler:
        def stop(self) -> None:
            raise RuntimeError("join hung")

    def boom(*_a, **_k):
        raise ValueError("bad")

    renderer, buf = _renderer()
    perf = _perf(renderer)
    perf._sampler = cast("Sampler", BadSampler())
    perf.register_phase("observe", [])
    monkeypatch.setattr(perf, "_boot_milestones", boom)
    monkeypatch.setattr(perf.record, "human_summary", boom)
    perf.finish(tmp_path / "missing.log", "20260930T000000Z")  # transcript unreadable
    notes = _report(runs)["notes"]
    assert any("sampler did not stop cleanly" in n and "join hung" in n for n in notes)
    assert any("per-VM boot milestones unavailable" in n for n in notes)
    assert any("observe readiness milestones unavailable" in n for n in notes)
    assert any("perf summary unavailable" in n for n in notes)
    assert buf.getvalue().count("perf: WARNING") == 4


def test_finish_warns_loudly_when_the_report_cannot_be_written(monkeypatch, tmp_path):
    def unwritable(ts: str) -> Path:
        raise PermissionError(f"read-only runs dir for {ts}")

    monkeypatch.setattr(perfrun, "report_path", unwritable)
    renderer, buf = _renderer()
    perf = _perf(renderer)
    perf.finish(tmp_path / "absent.log", "20260930T000000Z")  # must not raise
    assert "perf report perf-20260930T000000Z.json NOT written" in buf.getvalue()
    assert "read-only runs dir" in buf.getvalue()
