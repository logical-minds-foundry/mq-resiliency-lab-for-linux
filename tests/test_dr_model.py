from mqlab.dr.model import Bucket, MessageState, MessageFacts


def test_bucket_has_the_six_spec_buckets():
    assert {b.value for b in Bucket} == {
        "confirmed", "continued", "stranded",
        "lost_unprocessed", "ambiguous", "duplicated",
    }


def test_message_state_tristate():
    assert {s.value for s in MessageState} == {
        "never_sent", "in_pipeline", "confirmed",
    }


def test_message_facts_is_frozen_and_carries_identity():
    f = MessageFacts(
        seq=7, uuid="u7", firm_confirmed=False, dtcc_received=1,
        dtcc_replied=True, on_secondary=False, on_primary_disk=True,
    )
    assert f.seq == 7 and f.uuid == "u7"
    import dataclasses
    assert dataclasses.is_dataclass(f)
