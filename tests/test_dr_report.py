import dataclasses

import pytest

from mqlab.dr.model import Bucket, MessageFacts
from mqlab.dr.report import (
    SelfCorrectnessError,
    assert_self_correct,
    census,
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
