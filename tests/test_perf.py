from __future__ import annotations

import json

from mqlab.perf import NullSink, PerfRecord, PerfSink


def test_perf_record_collects_steps_and_serialises():
    rec = PerfRecord(stack="nativeha-ubuntu", started_at=1000.0)
    rec.add_step(phase="vms", label="vms up [1/3]", seconds=154.95, retries=0)
    rec.add_step(phase="vms", label="vms up [3/3]", seconds=310.0, retries=2)
    rec.add_milestone(name="opensearch_green", seconds=33 * 60)
    data = json.loads(rec.to_json())
    assert data["stack"] == "nativeha-ubuntu"
    assert data["phases"]["vms"]["seconds"] == 154.95 + 310.0
    assert data["phases"]["vms"]["retries"] == 2
    assert data["milestones"]["opensearch_green"] == 33 * 60
    assert "vms" in rec.human_summary()
    assert "opensearch_green" in rec.human_summary()


def test_perf_record_keeps_per_step_detail_in_phase_first_seen_order():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_step(phase="net", label="networks up", seconds=1.5, retries=0)
    rec.add_step(phase="vms", label="vms up [1/2]", seconds=10.0, retries=1)
    rec.add_step(phase="net", label="late net step", seconds=0.5, retries=0)
    data = json.loads(rec.to_json())
    assert list(data["phases"]) == ["net", "vms"]
    assert data["phases"]["net"]["steps"] == [
        {"label": "networks up", "seconds": 1.5, "retries": 0},
        {"label": "late net step", "seconds": 0.5, "retries": 0},
    ]
    assert data["phases"]["net"]["seconds"] == 2.0
    assert data["started_at"] == 0.0
    assert data["notes"] == []
    # to_json is deterministic for the same record.
    assert rec.to_json() == rec.to_json()


def test_perf_record_is_a_perf_sink():
    """A PerfRecord can be handed straight to run_steps as its sink (Task 3)."""
    rec = PerfRecord(stack="s", started_at=0.0)
    sink: PerfSink = rec
    sink.step("provision", "provision qm", 42.0, 1)
    data = json.loads(rec.to_json())
    assert data["phases"]["provision"] == {
        "seconds": 42.0,
        "retries": 1,
        "steps": [{"label": "provision qm", "seconds": 42.0, "retries": 1}],
    }


def test_human_summary_lists_unphased_steps_and_notes():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_step(phase="", label="legacy step", seconds=3.0, retries=0)
    rec.notes.append("guest san-a: steal sample unavailable (ssh timeout)")
    summary = rec.human_summary()
    assert "(unphased)" in summary
    assert "3.00s" in summary
    assert "steal sample unavailable" in summary
    assert json.loads(rec.to_json())["notes"] == [
        "guest san-a: steal sample unavailable (ssh timeout)"
    ]


def test_human_summary_of_an_empty_record_says_so():
    summary = PerfRecord(stack="s", started_at=0.0).human_summary()
    assert "no steps recorded" in summary
    assert "Milestones" not in summary
    assert "Notes" not in summary


def test_null_sink_accepts_and_discards():
    sink: PerfSink = NullSink()
    assert sink.step("vms", "vms up", 1.0, 0) is None
