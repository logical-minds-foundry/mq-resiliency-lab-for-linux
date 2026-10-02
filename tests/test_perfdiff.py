"""Tests for `mqlab.perfdiff` + `mqlab perf diff` (#1204, epic .github#275 Task 5).

The diff is a comparison aid, never a verdict: it reports per-phase / per-milestone
deltas and ratios (A minus B, A over B), names the dominant phase divergence, and —
when the host-contention `samples` key is present — each side's top vCPU-steal
contributors. A missing `samples` key (reports predating the sampler, #1203) is noted,
never fatal; a malformed report or unreadable file fails loud.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from mqlab import cli, perfdiff
from mqlab.perf import PerfRecord
from mqlab.perfdiff import PerfDiffError, diff, load
from mqlab.perfsampler import GuestSample, LocalSample


def _record(stack: str, phases: dict[str, float], milestones: dict[str, float]) -> dict[str, Any]:
    rec = PerfRecord(stack=stack, started_at=0.0)
    for name, seconds in phases.items():
        rec.add_step(phase=name, label=f"{name} step", seconds=seconds, retries=0)
    for name, seconds in milestones.items():
        rec.add_milestone(name, seconds)
    data: dict[str, Any] = json.loads(rec.to_json())
    return data


def _macos_vs_cloud() -> tuple[dict[str, Any], dict[str, Any]]:
    a = _record(
        "nativeha-ubuntu",
        {"net": 5.0, "vms": 600.0, "provision": 900.0, "observe": 33 * 60.0},
        {"opensearch_green": 30 * 60.0, "only_on_a": 12.0},
    )
    b = _record(
        "nativeha-ubuntu",
        {"net": 4.0, "vms": 300.0, "provision": 700.0, "observe": 2 * 60.0},
        {"opensearch_green": 60.0, "only_on_b": 7.0},
    )
    return a, b


# --- the plan's Step 1 acceptance -----------------------------------------------------


def test_observe_delta_ratio_and_dominant_divergence():
    a, b = _macos_vs_cloud()
    report = diff(a, b)
    observe = next(p for p in report.phases if p.name == "observe")
    assert observe.a == 33 * 60.0
    assert observe.b == 2 * 60.0
    assert observe.delta == 31 * 60.0
    assert observe.ratio == pytest.approx(16.5)
    assert report.dominant is not None
    assert report.dominant.name == "observe"


def test_milestone_present_on_one_side_only_is_tolerated():
    a, b = _macos_vs_cloud()
    report = diff(a, b)
    by_name = {m.name: m for m in report.milestones}
    assert list(by_name) == ["opensearch_green", "only_on_a", "only_on_b"]
    assert by_name["opensearch_green"].delta == 29 * 60.0
    assert by_name["only_on_a"].b is None
    assert by_name["only_on_a"].delta is None
    assert by_name["only_on_a"].ratio is None
    assert by_name["only_on_b"].a is None
    text = report.render()
    assert "only_on_a" in text
    assert "only_on_b" in text


def test_render_is_a_table_with_no_verdict():
    a, b = _macos_vs_cloud()
    text = diff(a, b).render(label_a="mac.json", label_b="cloud.json")
    assert "A = mac.json" in text
    assert "B = cloud.json" in text
    assert "Dominant divergence: observe" in text
    assert "16.50x" in text
    for verdict in ("PASS", "FAIL", "better", "worse"):
        assert verdict not in text


# --- phases ---------------------------------------------------------------------------


def test_phase_on_one_side_only_has_no_delta_and_is_not_dominant():
    a = _record("s", {"vms": 10.0, "observe": 5000.0}, {})
    b = _record("s", {"vms": 4.0}, {})
    report = diff(a, b)
    observe = next(p for p in report.phases if p.name == "observe")
    assert observe.b is None
    assert observe.delta is None
    assert report.dominant is not None
    assert report.dominant.name == "vms"
    assert "observe" in report.render()


def test_phase_only_on_b_is_listed_after_a_phases():
    a = _record("s", {"vms": 10.0}, {})
    b = _record("s", {"net": 1.0, "vms": 4.0}, {})
    assert [p.name for p in diff(a, b).phases] == ["vms", "net"]


def test_prereq_phases_diff_against_a_report_that_predates_them():
    # #1248 adds `prereq:<phase>` phases. A newer report (A) diffs against an older one
    # (B) without them: they are listed with `—` for B, never dominant, never an error.
    a = _record(
        "nativeha-ubuntu",
        {"preflight": 2.0, "prereq:vms": 9.0, "vms": 300.0, "prereq:provision": 50.0},
        {},
    )
    b = _record("nativeha-ubuntu", {"preflight": 2.5, "vms": 310.0}, {})
    report = diff(a, b)
    names = [p.name for p in report.phases]
    assert names == ["preflight", "prereq:vms", "vms", "prereq:provision"]
    prereq = next(p for p in report.phases if p.name == "prereq:provision")
    assert (prereq.a, prereq.b, prereq.delta) == (50.0, None, None)
    assert report.dominant is not None
    assert report.dominant.name == "vms"
    rendered = report.render()
    assert "prereq:provision" in rendered
    assert "prereq:vms" in rendered


def test_identical_reports_have_no_dominant_divergence():
    a = _record("s", {"vms": 10.0}, {})
    report = diff(a, a)
    assert report.dominant is None
    assert "Dominant divergence: none" in report.render()


def test_zero_b_seconds_gives_no_ratio():
    a = _record("s", {"vms": 10.0}, {})
    b = _record("s", {"vms": 0.0}, {})
    phase = diff(a, b).phases[0]
    assert phase.delta == 10.0
    assert phase.ratio is None


def test_retries_are_carried_per_side_and_unphased_is_labelled():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_step(phase="", label="x", seconds=3.0, retries=2)
    a: dict[str, Any] = json.loads(rec.to_json())
    report = diff(a, a)
    assert report.phases[0].a_retries == 2
    assert report.phases[0].b_retries == 2
    assert "(unphased)" in report.render()


def test_retries_absent_on_one_side_is_none():
    a = _record("s", {"vms": 10.0}, {})
    b = _record("s", {}, {})
    phase = diff(a, b).phases[0]
    assert phase.a_retries == 0
    assert phase.b_retries is None


def test_stack_mismatch_is_noted_not_fatal():
    a = _record("nativeha-ubuntu", {"vms": 1.0}, {})
    b = _record("rdqm-rhel", {"vms": 1.0}, {})
    report = diff(a, b)
    assert any("stacks differ" in n for n in report.notes)


def test_record_notes_are_surfaced_per_side():
    a = _record("s", {}, {})
    a["notes"] = ["sample unavailable: obs"]
    report = diff(a, _record("s", {}, {}))
    assert report.a_notes == ["sample unavailable: obs"]
    assert report.b_notes == []
    assert "sample unavailable: obs" in report.render()


def test_empty_reports_render():
    text = diff(_record("s", {}, {}), _record("s", {}, {})).render()
    assert "(no phases)" in text
    assert "(no milestones)" in text


# --- malformed input fails loud -------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: r.pop("phases"), "'phases'"),
        (lambda r: r.update(phases=[]), "'phases'"),
        (lambda r: r.pop("milestones"), "'milestones'"),
        (lambda r: r.update(milestones={"m": "slow"}), "milestone 'm'"),
        (lambda r: r.update(milestones={"m": True}), "milestone 'm'"),
        (lambda r: r["phases"].update(vms=5), "phase 'vms'"),
        (lambda r: r["phases"]["vms"].update(seconds="1"), "phase 'vms'"),
        (lambda r: r["phases"]["vms"].update(retries=1.5), "phase 'vms'"),
        (lambda r: r["phases"]["vms"].update(failed="1"), "phase 'vms'"),
        (lambda r: r["phases"]["vms"].update(failed=True), "phase 'vms'"),
        (lambda r: r.update(stack=3), "'stack'"),
        (lambda r: r.update(notes="oops"), "'notes'"),
        (lambda r: r.update(notes=[1]), "'notes'"),
    ],
)
def test_malformed_report_raises(mutate, fragment):
    good = _record("s", {"vms": 1.0}, {})
    bad = _record("s", {"vms": 1.0}, {})
    mutate(bad)
    with pytest.raises(PerfDiffError, match="report B") as exc:
        diff(good, bad)
    assert fragment in str(exc.value)


def test_non_mapping_report_raises():
    not_a_mapping: Any = []
    with pytest.raises(PerfDiffError, match="report A"):
        diff(not_a_mapping, _record("s", {}, {}))


def test_missing_optional_stack_and_notes_are_tolerated():
    a = _record("s", {"vms": 1.0}, {})
    del a["stack"], a["notes"]
    report = diff(a, a)
    assert report.a_stack is None
    assert report.a_notes == []
    assert "(unknown stack)" in report.render()


# --- host-contention samples (shape owned by #1203; read defensively) -----------------
# PerfRecord.to_json() always emits `samples` (empty when nothing was sampled), so a test
# about a report that predates the sampler deletes the key explicitly.


def _predates_sampler(rec: dict[str, Any]) -> dict[str, Any]:
    """A report written before #1203: PerfRecord.to_json() now always emits `samples`."""
    del rec["samples"]
    return rec


def test_missing_samples_is_noted_on_each_side():
    a, b = _macos_vs_cloud()
    report = diff(_predates_sampler(a), _predates_sampler(b))
    assert report.steal_a is None
    assert report.steal_b is None
    assert sum("no host-contention samples" in n for n in report.notes) == 2
    assert "steal contributors: n/a" in report.render()


def _sample(guests: dict[str, Any]) -> dict[str, Any]:
    return {"t": 0.0, "host_cpu": 50.0, "host_iowait": 5.0, "guests": guests}


def test_top_steal_contributors_by_mean_steal():
    a, b = _macos_vs_cloud()
    a["samples"] = [
        _sample({"obs": {"steal_pct": 40.0}, "qm1": {"steal": 10.0}, "app": 2.0}),
        _sample({"obs": {"steal_pct": 20.0}, "qm1": {"steal": 30.0}, "app": 4.0}),
    ]
    report = diff(a, _predates_sampler(b))
    assert report.steal_a is not None
    assert [(c.guest, c.mean_steal_pct, c.samples) for c in report.steal_a] == [
        ("obs", 30.0, 2),
        ("qm1", 20.0, 2),
        ("app", 3.0, 2),
    ]
    assert report.steal_b is None
    text = report.render()
    assert "obs" in text
    assert "30.0%" in text


def test_steal_contributors_are_capped_and_tie_broken_by_name():
    rec = _record("s", {}, {})
    rec["samples"] = [_sample({f"g{i}": 1.0 for i in range(perfdiff.TOP_STEAL + 2)})]
    report = diff(rec, rec)
    assert report.steal_a is not None
    assert len(report.steal_a) == perfdiff.TOP_STEAL
    assert report.steal_a[0].guest == "g0"


def test_unreadable_sample_entries_are_skipped_and_noted():
    rec = _record("s", {}, {})
    rec["samples"] = [
        "garbage",
        {"t": 1.0},
        _sample({"obs": {"load": 3.0}, "qm1": True, "app": 5.0}),
    ]
    report = diff(rec, _record("s", {}, {}))
    assert report.steal_a is not None
    assert [c.guest for c in report.steal_a] == ["app"]
    assert any("A: 4 sample entr(y/ies)" in n for n in report.notes)


def test_samples_not_a_list_is_noted():
    rec = _record("s", {}, {})
    rec["samples"] = {"oops": 1}
    report = diff(rec, _record("s", {}, {}))
    assert report.steal_a is None
    assert any("A: 'samples' is not a list" in n for n in report.notes)


def test_empty_samples_is_noted_and_renders_none():
    rec = _record("s", {}, {})
    rec["samples"] = []
    report = diff(rec, rec)
    assert report.steal_a == []
    assert any("A: 'samples' is empty" in n for n in report.notes)
    assert "(no readable steal samples)" in report.render()


def test_current_format_record_with_no_samples_gives_empty_contributors():
    """A real post-#1203 PerfRecord that sampled nothing emits `samples: []`."""
    a, b = _macos_vs_cloud()
    assert a["samples"] == []
    report = diff(a, b)
    assert report.steal_a == []
    assert report.steal_b == []
    assert any("A: 'samples' is empty" in n for n in report.notes)
    assert any("B: 'samples' is empty" in n for n in report.notes)
    assert not any("no host-contention samples" in n for n in report.notes)


def test_genuine_sampler_output_is_read_and_baselines_are_not_unreadable():
    """Feed PerfRecord.add_sample output (the real #1203 shape) through diff()."""
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_sample(0.0, None, None, {"obs": GuestSample(None), "qm1": GuestSample(None)})
    rec.add_sample(5.0, 80.0, 10.0, {"obs": GuestSample(40.0), "qm1": GuestSample(5.0)}, 8)
    rec.add_sample(10.0, 70.0, 20.0, {"obs": GuestSample(20.0)}, 8)
    a: dict[str, Any] = json.loads(rec.to_json())
    assert a["samples"][0]["guests"]["obs"]["steal"] is None
    report = diff(a, _record("s", {}, {}))
    assert report.steal_a is not None
    assert [(c.guest, c.mean_steal_pct, c.samples) for c in report.steal_a] == [
        ("obs", 30.0, 2),
        ("qm1", 5.0, 1),
    ]
    assert not any("A:" in n and "no readable steal" in n for n in report.notes)


# --- #1215: failed steps + the host/Vergil-VM means ------------------------------------


def test_failed_steps_are_counted_per_side_and_flagged_in_the_table():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.step("observe", "render targets", 0.5, 0)
    rec.step("observe", "site-obs", 584.0, 0, ok=False)
    a: dict[str, Any] = json.loads(rec.to_json())
    b = _record("s", {"observe": 120.0}, {})
    del b["phases"]["observe"]["failed"]  # a report predating #1215: no `failed` key
    report = diff(a, b)
    (observe,) = report.phases
    assert (observe.a, observe.b) == (584.5, 120.0)  # the failed step's time is IN it
    assert (observe.a_failed, observe.b_failed) == (1, 0)
    row = next(ln for ln in report.render().splitlines() if ln.strip().startswith("observe"))
    assert row.endswith("0/0  FAILED steps A/B 1/0")


def test_no_failed_steps_leaves_the_row_unflagged():
    a, b = _macos_vs_cloud()
    assert "FAILED" not in diff(a, b).render()


def test_host_and_vergil_vm_means_per_side():
    rec = PerfRecord(stack="s", started_at=0.0)
    local = LocalSample(steal_pct=None, busy_pct=None, iowait_pct=None)
    rec.add_sample(0.0, 80.0, 10.0, {}, 8, local=local)  # baseline self reading
    rec.add_sample(5.0, 70.0, 20.0, {}, 8, local=LocalSample(9.0, 50.0, 3.0))
    rec.add_sample(10.0, None, None, {})  # both probes failed this tick
    a: dict[str, Any] = json.loads(rec.to_json())
    b = _record("s", {}, {})
    report = diff(a, b)
    assert report.host_a == {
        "cpu": 75.0,
        "iowait": 15.0,
        "self_steal": 9.0,
        "self_busy": 50.0,
        "self_iowait": 3.0,
    }
    assert report.host_b is None
    text = report.render()
    assert "A host means: cpu 75.0%  iowait 15.0%  self_steal 9.0%" in text
    assert "B host means: n/a (no samples)" in text


def test_a_pre_1215_report_has_no_self_readings():
    rec = _record("s", {}, {})
    rec["samples"] = [{"t": 0.0, "host": {"cpu": 5.0, "iowait": 0.5, "cpus": 24}, "guests": {}}]
    report = diff(rec, rec)
    assert report.host_a is not None
    assert report.host_a["self_steal"] is None
    assert "self_steal —" in report.render()


def test_samples_without_a_host_mapping_are_skipped_and_noted():
    rec = _record("s", {}, {})
    rec["samples"] = ["garbage", {"t": 1.0, "host": {"cpu": 4.0}}]
    report = diff(rec, _record("s", {}, {}))
    assert report.host_a is not None
    assert report.host_a["cpu"] == 4.0
    assert "A: 1 sample(s) had no host mapping; skipped" in report.notes


# --- load() ----------------------------------------------------------------------------


def test_load_reads_a_perf_json(tmp_path):
    path = tmp_path / "perf.json"
    path.write_text(json.dumps(_record("s", {"vms": 1.0}, {})))
    assert load(path)["stack"] == "s"


def test_load_missing_file_fails_loud(tmp_path):
    with pytest.raises(PerfDiffError, match="cannot read perf report"):
        load(tmp_path / "nope.json")


def test_load_invalid_json_fails_loud(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(PerfDiffError, match="not valid JSON"):
        load(path)


def test_load_non_object_json_fails_loud(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]")
    with pytest.raises(PerfDiffError, match="JSON object"):
        load(path)


# --- CLI: mqlab perf diff --------------------------------------------------------------


def _write_pair(tmp_path) -> tuple[str, str]:
    a, b = _macos_vs_cloud()
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    return str(pa), str(pb)


def test_cli_perf_diff_prints_the_table(tmp_path):
    pa, pb = _write_pair(tmp_path)
    result = CliRunner().invoke(cli.app, ["perf", "diff", pa, pb])
    assert result.exit_code == 0, result.output
    assert "Dominant divergence: observe" in result.output
    assert f"A = {pa}" in result.output


def test_cli_perf_diff_missing_file_exits_2_naming_the_command(tmp_path):
    pa, _ = _write_pair(tmp_path)
    missing = str(tmp_path / "missing.json")
    result = CliRunner().invoke(cli.app, ["perf", "diff", pa, missing])
    assert result.exit_code == 2
    assert "mqlab perf diff" in result.output
    assert "cannot read perf report" in result.output


def test_cli_perf_diff_malformed_report_exits_2(tmp_path):
    pa, pb = _write_pair(tmp_path)
    (tmp_path / "b.json").write_text(json.dumps({"stack": "s"}))
    result = CliRunner().invoke(cli.app, ["perf", "diff", pa, pb])
    assert result.exit_code == 2
    assert "report B" in result.output
