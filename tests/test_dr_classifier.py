import dataclasses

from mqlab.dr.classifier import classify
from mqlab.dr.model import Bucket, MessageFacts

_BASE = MessageFacts(
    seq=1,
    uuid="u",
    firm_confirmed=False,
    dtcc_received=0,
    dtcc_replied=False,
    on_secondary=False,
    on_primary_disk=False,
)


def _facts(**kw) -> MessageFacts:
    return dataclasses.replace(_BASE, **kw)


def test_duplicated_when_dtcc_received_twice():
    assert classify(_facts(dtcc_received=2, dtcc_replied=True)) is Bucket.DUPLICATED


def test_confirmed_when_firm_got_reply():
    assert (
        classify(_facts(firm_confirmed=True, dtcc_received=1, dtcc_replied=True, on_secondary=True))
        is Bucket.CONFIRMED
    )


def test_continued_when_replicated_to_secondary():
    assert (
        classify(_facts(on_secondary=True, dtcc_received=1, dtcc_replied=True)) is Bucket.CONTINUED
    )


def test_ambiguous_when_dtcc_processed_but_not_replicated_or_confirmed():
    assert classify(_facts(dtcc_received=1, dtcc_replied=True)) is Bucket.AMBIGUOUS


def test_stranded_when_on_dead_primary_only():
    assert classify(_facts(on_primary_disk=True)) is Bucket.STRANDED


def test_lost_when_gone_everywhere():
    assert classify(_facts()) is Bucket.LOST_UNPROCESSED


def test_precedence_duplicated_beats_confirmed():
    # a duplicate is a duplicate even if the firm also got a reply
    assert classify(_facts(firm_confirmed=True, dtcc_received=2)) is Bucket.DUPLICATED


def test_precedence_confirmed_beats_continued():
    assert (
        classify(_facts(firm_confirmed=True, on_secondary=True, dtcc_received=1))
        is Bucket.CONFIRMED
    )


def test_precedence_ambiguous_beats_stranded():
    # reached DTCC once AND still on the primary disk -> Ambiguous (resend = dup)
    assert classify(_facts(dtcc_received=1, on_primary_disk=True)) is Bucket.AMBIGUOUS


def test_classify_all_pairs_each_fact_with_its_bucket():
    from mqlab.dr.classifier import classify_all

    facts = [_facts(seq=1, firm_confirmed=True), _facts(seq=2, on_primary_disk=True)]
    pairs = classify_all(facts)
    assert pairs == [(facts[0], Bucket.CONFIRMED), (facts[1], Bucket.STRANDED)]
