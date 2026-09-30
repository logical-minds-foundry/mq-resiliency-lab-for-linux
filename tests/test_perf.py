from __future__ import annotations

import json

from mqlab.perf import NullSink, PerfRecord, PerfSink
from mqlab.perfsampler import GuestSample


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


def test_note_appends_to_notes_in_order():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.note("first")
    rec.note("second")
    assert rec.notes == ["first", "second"]
    assert json.loads(rec.to_json())["notes"] == ["first", "second"]


def test_add_sample_serialises_after_the_existing_keys():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_sample(
        15.0,
        host_cpu=40.5,
        host_iowait=2.0,
        guests={"obs": GuestSample(steal_pct=3.25), "qm-a": GuestSample(steal_pct=None)},
        host_cpus=24,
    )
    rec.add_sample(30.0, host_cpu=None, host_iowait=None, guests={})
    data = json.loads(rec.to_json())
    # The Task-1 keys keep their order; samples is appended last.
    assert list(data) == ["stack", "started_at", "phases", "milestones", "notes", "samples"]
    assert data["samples"] == [
        {
            "t": 15.0,
            "host": {"cpu": 40.5, "iowait": 2.0, "cpus": 24},
            "guests": {"obs": {"steal": 3.25}, "qm-a": {"steal": None}},
        },
        {"t": 30.0, "host": {"cpu": None, "iowait": None, "cpus": None}, "guests": {}},
    ]


def test_add_sample_copies_the_guest_map():
    rec = PerfRecord(stack="s", started_at=0.0)
    guests = {"obs": GuestSample(steal_pct=1.0)}
    rec.add_sample(1.0, host_cpu=1.0, host_iowait=0.0, guests=guests)
    guests["late"] = GuestSample(steal_pct=2.0)
    assert list(json.loads(rec.to_json())["samples"][0]["guests"]) == ["obs"]


def test_human_summary_renders_peak_and_mean_steal_per_guest_and_host_load():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_sample(
        15.0, host_cpu=10.0, host_iowait=1.0, guests={"obs": GuestSample(steal_pct=None)}
    )
    rec.add_sample(30.0, host_cpu=50.0, host_iowait=5.0, guests={"obs": GuestSample(steal_pct=4.0)})
    rec.add_sample(
        45.0,
        host_cpu=None,
        host_iowait=None,
        guests={"obs": GuestSample(steal_pct=8.0), "qm-a": GuestSample(steal_pct=None)},
    )
    lines = rec.human_summary().splitlines()
    assert "Samples: 3" in lines
    host_cpu = next(ln for ln in lines if "host cpu" in ln)
    assert "mean 30.0%" in host_cpu
    assert "peak 50.0%" in host_cpu
    assert "(2 reading(s))" in host_cpu
    host_io = next(ln for ln in lines if "host iowait" in ln)
    assert "mean 3.0%" in host_io
    assert "peak 5.0%" in host_io
    obs = next(ln for ln in lines if "steal obs" in ln)
    assert "mean 6.0%" in obs
    assert "peak 8.0%" in obs
    # a guest with only baseline readings says so rather than inventing a number.
    qm = next(ln for ln in lines if "steal qm-a" in ln)
    assert "no reading" in qm


def test_human_summary_without_any_host_reading_says_so():
    rec = PerfRecord(stack="s", started_at=0.0)
    rec.add_sample(15.0, host_cpu=None, host_iowait=None, guests={})
    lines = rec.human_summary().splitlines()
    assert "no reading" in next(ln for ln in lines if "host cpu" in ln)


def test_human_summary_omits_samples_section_when_none_taken():
    assert "Samples" not in PerfRecord(stack="s", started_at=0.0).human_summary()
