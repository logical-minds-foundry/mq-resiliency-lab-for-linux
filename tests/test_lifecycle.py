from __future__ import annotations

from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, OFF, RUNNING, classify, classify_net

STATES = {"lab_nha-ubuntu-a1": "running", "lab_nha-ubuntu-a2": "shut off", "lab_obs": "paused"}
NET_STATES = {"net-data-a": "active", "net-data-b": "inactive"}


def test_classify_running():
    assert classify(STATES, "nha-ubuntu-a1") == RUNNING


def test_classify_off_for_shut_off_and_other_non_running():
    assert classify(STATES, "nha-ubuntu-a2") == OFF
    assert classify(STATES, "obs") == OFF  # paused etc. count as not-running


def test_classify_absent_when_no_domain():
    assert classify(STATES, "rdqm-a1") == ABSENT


def test_classify_net_active():
    assert classify_net(NET_STATES, "net-data-a") == ACTIVE


def test_classify_net_inactive_for_non_active():
    assert classify_net(NET_STATES, "net-data-b") == INACTIVE


def test_classify_net_absent_when_no_network():
    assert classify_net(NET_STATES, "net-wan") == ABSENT
