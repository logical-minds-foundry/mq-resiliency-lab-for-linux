from __future__ import annotations

import pytest

from mqlab.dr import ScenarioReport, build_report
from mqlab.dr.model import MessageFacts
from mqlab.runreport import RunMetadata, RunReport, capture_metadata


def _confirmed_report(arm: str) -> ScenarioReport:
    facts = [
        MessageFacts(
            seq=1,
            uuid="u1",
            firm_confirmed=True,
            dtcc_received=1,
            dtcc_replied=True,
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
    md = RunMetadata("s", "c", "t", "d", {"k": "v"})
    assert md.to_dict() == {
        "setup": "s",
        "commit": "c",
        "timestamp": "t",
        "config_digest": "d",
        "versions": {"k": "v"},
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
