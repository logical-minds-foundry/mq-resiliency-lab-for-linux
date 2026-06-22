from __future__ import annotations

import json

import pytest

from mqlab.dr import ScenarioReport, build_report
from mqlab.dr.model import MessageFacts
from mqlab.runreport import (
    RunMetadata,
    RunReport,
    append_index,
    capture_metadata,
    write_bundle,
)


def _confirmed_report(arm: str) -> ScenarioReport:
    facts = [
        MessageFacts(
            seq=1,
            uuid="u1",
            app_confirmed=True,
            svc_received=1,
            svc_replied=True,
            on_secondary=False,
            on_primary_disk=False,
        )
    ]
    return build_report("BASELINE", arm, facts, peak_exposure=0)


def test_capture_metadata_assembles_from_readers() -> None:
    md = capture_metadata(
        "distributed",
        "20260615T143000Z",
        commit_reader=lambda: "abc123",
        digest_reader=lambda: "deadbeef",
        version_reader=lambda: {"vagrant": "2.4.1"},
    )
    assert md == RunMetadata(
        setup="distributed",
        commit="abc123",
        timestamp="20260615T143000Z",
        config_digest="deadbeef",
        versions={"vagrant": "2.4.1"},
    )


def test_capture_metadata_propagates_reader_failure() -> None:
    def boom() -> str:
        raise RuntimeError("git unavailable")

    with pytest.raises(RuntimeError, match="git unavailable"):
        capture_metadata(
            "distributed",
            "20260615T143000Z",
            commit_reader=boom,
            digest_reader=lambda: "d",
            version_reader=dict,
        )


def test_run_metadata_to_dict_round_trips() -> None:
    md = RunMetadata("s", "c", "t", "d", {"k": "v"}, manifest="s/default")
    assert md.to_dict() == {
        "setup": "s",
        "commit": "c",
        "timestamp": "t",
        "config_digest": "d",
        "versions": {"k": "v"},
        "manifest": "s/default",
    }


def test_run_report_to_dict_nests_metadata_and_scenarios() -> None:
    md = RunMetadata("distributed", "c", "t", "d", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    d = report.to_dict()
    assert d["metadata"]["setup"] == "distributed"
    assert d["scenarios"][0]["scenario_id"] == "BASELINE"
    assert d["scenarios"][0]["rpo_zero"] is True


def test_run_report_markdown_has_header_and_scenario() -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {"vagrant": "2.4.1"})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    text = report.to_markdown()
    assert "# Run report — distributed @ 20260615T143000Z" in text
    assert "Commit: `abc123`" in text
    assert "vagrant=2.4.1" in text
    assert "### BASELINE — arm pcmk-ubuntu" in text


def test_run_report_markdown_handles_no_versions() -> None:
    md = RunMetadata("distributed", "c", "t", "d", {})
    report = RunReport(metadata=md, scenarios=[])
    assert "Versions: (none)" in report.to_markdown()


def test_write_bundle_creates_json_and_markdown(tmp_path) -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    bundle = write_bundle(report, tmp_path)
    assert bundle == tmp_path / "20260615T143000Z-distributed"
    loaded = json.loads((bundle / "report.json").read_text())
    assert loaded["metadata"]["commit"] == "abc123"
    assert "# Run report — distributed" in (bundle / "report.md").read_text()


def test_append_index_writes_one_jsonl_line_per_call(tmp_path) -> None:
    md = RunMetadata("distributed", "abc123", "20260615T143000Z", "deadbeef", {})
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    bundle = write_bundle(report, tmp_path)
    append_index(report, bundle, tmp_path)
    append_index(report, bundle, tmp_path)
    lines = (tmp_path / "index.jsonl").read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[0])
    assert entry["setup"] == "distributed"
    assert entry["commit"] == "abc123"
    assert entry["bundle"] == "20260615T143000Z-distributed"
    assert entry["verdicts"] == {"BASELINE": True}


def test_capture_metadata_includes_manifest() -> None:
    md = capture_metadata(
        "distributed-pcmk-ubuntu",
        "20260618T000000Z",
        commit_reader=lambda: "abc",
        digest_reader=lambda: "def",
        version_reader=lambda: {"mq": "9.4.5.0"},
        manifest_reader=lambda: "distributed-pcmk-ubuntu/default",
    )
    assert md.manifest == "distributed-pcmk-ubuntu/default"
    assert md.versions == {"mq": "9.4.5.0"}


def test_capture_metadata_manifest_defaults_empty() -> None:
    md = capture_metadata(
        "s",
        "t",
        commit_reader=lambda: "c",
        digest_reader=lambda: "d",
        version_reader=lambda: {},
    )
    assert md.manifest == ""


def test_to_markdown_shows_manifest() -> None:
    md = RunMetadata("s", "c", "t", "d", {}, manifest="s/default")
    report = RunReport(metadata=md, scenarios=[_confirmed_report("pcmk-ubuntu")])
    assert "- Manifest: `s/default`" in report.to_markdown()


def test_config_digest_is_manifest_sensitive(tmp_path) -> None:
    from mqlab.runreport import read_config_digest

    man = tmp_path / "sel.yaml"
    man.write_text("manifest: default\n")
    before = read_config_digest([man])
    man.write_text("manifest: repro-945\n")
    assert read_config_digest([man]) != before  # digest tracks the pinned manifest
