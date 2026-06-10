from __future__ import annotations

from mqlab.lifecycle import ABSENT, OFF, RUNNING, classify

STATES = {"lab_pcmk-a1": "running", "lab_pcmk-a2": "shut off", "lab_san-a": "paused"}


def test_classify_running():
    assert classify(STATES, "pcmk-a1") == RUNNING


def test_classify_off_for_shut_off_and_other_non_running():
    assert classify(STATES, "pcmk-a2") == OFF
    assert classify(STATES, "san-a") == OFF  # paused etc. count as not-running


def test_classify_absent_when_no_domain():
    assert classify(STATES, "rdqm-a1") == ABSENT
