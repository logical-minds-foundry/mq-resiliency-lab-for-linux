import dataclasses

import pytest

from mqlab.dr.model import Bucket, MessageFacts
from mqlab.dr.report import (
    SelfCorrectnessError,
    assert_self_correct,
    build_report,
    census,
    cross_arm,
    loss_window,
)


def _f(seq, **kw) -> MessageFacts:
    base = MessageFacts(
        seq=seq,
        uuid=f"u{seq}",
        firm_confirmed=False,
        dtcc_received=0,
        dtcc_replied=False,
        on_secondary=False,
        on_primary_disk=False,
    )
    return dataclasses.replace(base, **kw)


def _mixed():
    return [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),  # Confirmed
        _f(2, on_secondary=True, dtcc_received=1, dtcc_replied=True),  # Continued
        _f(3, on_primary_disk=True),  # Stranded
        _f(4, dtcc_received=1, dtcc_replied=True),  # Ambiguous
        _f(5),  # Lost
    ]


def test_census_counts_each_bucket():
    c = census(_mixed())
    assert c[Bucket.CONFIRMED] == 1
    assert c[Bucket.CONTINUED] == 1
    assert c[Bucket.STRANDED] == 1
    assert c[Bucket.AMBIGUOUS] == 1
    assert c[Bucket.LOST_UNPROCESSED] == 1
    assert c[Bucket.DUPLICATED] == 0


def test_loss_window_spans_non_safe_buckets_by_sequence():
    # at-risk = stranded(3), ambiguous(4), lost(5) -> seqs 3..5, count 3
    assert loss_window(_mixed()) == (3, 5, 3)


def test_loss_window_none_when_clean():
    clean = [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)]
    assert loss_window(clean) is None


def test_self_correct_passes_when_all_confirmed():
    clean = [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
        _f(2, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
    ]
    assert_self_correct(clean)  # must not raise


def test_self_correct_raises_on_any_non_confirmed():
    dirty = [
        _f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True),
        _f(2, dtcc_received=1, dtcc_replied=True),  # Ambiguous in a no-fault run!
    ]
    with pytest.raises(SelfCorrectnessError) as exc:
        assert_self_correct(dirty)
    assert "2" in str(exc.value)  # names the offending seq


def test_self_correct_raises_on_duplicate():
    dirty = [_f(1, firm_confirmed=True, dtcc_received=2, dtcc_replied=True)]
    with pytest.raises(SelfCorrectnessError):
        assert_self_correct(dirty)


def test_build_report_carries_identity_census_window_and_verdict():
    facts = _mixed()
    rep = build_report(scenario_id="DR-FORCE-1", arm="C", facts=facts, peak_exposure=4)
    assert rep.scenario_id == "DR-FORCE-1"
    assert rep.arm == "C"
    assert rep.peak_exposure == 4
    assert rep.window == (3, 5, 3)
    assert rep.rpo_zero is False  # there is at-risk loss
    d = rep.to_dict()
    assert d["scenario_id"] == "DR-FORCE-1"
    assert d["census"]["ambiguous"] == 1
    assert "DR-FORCE-1" in rep.to_markdown()


def test_build_report_rpo_zero_when_clean():
    clean = [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)]
    rep = build_report("HA-1", "C", clean, peak_exposure=0)
    assert rep.rpo_zero is True
    assert rep.window is None
    # markdown with no loss window omits the "Loss window" line
    assert "Loss window" not in rep.to_markdown()


def test_cross_arm_pairs_same_scenario():
    facts = _mixed()
    c = build_report("DR-FORCE-2", "C", facts, peak_exposure=4)
    d = build_report("DR-FORCE-2", "D", facts, peak_exposure=4)
    comp = cross_arm(c, d)
    assert comp["scenario_id"] == "DR-FORCE-2"
    assert comp["C"]["census"]["stranded"] == 1
    assert comp["D"]["census"]["stranded"] == 1


def test_cross_arm_rejects_mismatched_scenarios():
    c = build_report("DR-FORCE-2", "C", _mixed(), peak_exposure=4)
    d = build_report("DR-FORCE-3", "D", _mixed(), peak_exposure=4)
    with pytest.raises(ValueError, match="same scenario"):
        cross_arm(c, d)


def test_report_carries_section7_honesty_fields():
    rep = build_report(
        "DR-FORCE-1",
        "C",
        _mixed(),
        peak_exposure=4,
        exposure_at_fault=3,
        rto_seconds=69.0,
        intervention_required=True,
        integrity_anomaly=False,
        diagnostics_captured=True,
    )
    assert rep.exposure_at_fault == 3
    assert rep.rto_seconds == 69.0
    assert rep.intervention_required is True
    assert rep.diagnostics_captured is True
    d = rep.to_dict()
    assert d["rto_seconds"] == 69.0
    assert d["exposure_at_fault"] == 3
    assert d["intervention_required"] is True
    assert d["integrity_anomaly"] is False
    assert d["diagnostics_captured"] is True
    md = rep.to_markdown()
    assert "RTO" in md and "diagnostics" in md.lower()


def test_report_honesty_fields_default_sensibly():
    rep = build_report(
        "HA-1",
        "C",
        [_f(1, firm_confirmed=True, dtcc_received=1, dtcc_replied=True)],
        peak_exposure=0,
    )
    assert rep.rto_seconds is None
    assert rep.exposure_at_fault is None
    assert rep.intervention_required is False
    assert rep.integrity_anomaly is False
    assert rep.diagnostics_captured is False
