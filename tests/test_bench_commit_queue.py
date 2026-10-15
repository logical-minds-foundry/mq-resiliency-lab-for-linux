"""Guard: mq-bench PUTs to its own BENCH.COMMIT queue, authorized on nativeha-ubuntu (#1512).

mq-bench used to PUT to APP.REPLY. nativeha-ubuntu applies the stage-2 least-privilege
posture, where mqapp is get-only on APP.REPLY, so every commit failed with 2035 there.
This pins the fix:

- the wrapper's queue is BENCH.COMMIT, and both Native HA arms define it;
- nativeha-ubuntu grants mqapp +put +inq on it, from a list kept apart from the shared
  authz_grants (the pcmk/rdqm arms, which never define BENCH.COMMIT, must not
  setmqaut it), and no other playbook loops over that list;
- APP.REPLY's posture is unchanged: mqapp still has no +put there.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ANSIBLE = Path(__file__).resolve().parents[1] / "ansible"
QUEUE = "BENCH.COMMIT"
NATIVE_HA_ARMS = ("site-nativeha.yml", "site-nativeha-ubuntu.yml")
BENCH_DEFAULTS = ANSIBLE / "roles" / "bench-client" / "defaults" / "main.yml"
BENCH_GRANT = {
    "group": "mqapp",
    "object_type": "queue",
    "object": QUEUE,
    "authorities": "+put +inq",
}
GRANT_LOOP = 'loop: "{{ authz_grants + authz_bench_grants }}"'


def _authz() -> dict[str, Any]:
    return yaml.safe_load((ANSIBLE / "group_vars" / "all" / "authz.yml").read_text())


def test_wrapper_targets_the_bench_queue() -> None:
    assert yaml.safe_load(BENCH_DEFAULTS.read_text())["bench_client_queue"] == QUEUE


def test_both_native_ha_arms_define_the_bench_queue() -> None:
    pattern = re.compile(rf"DEFINE QLOCAL\({re.escape(QUEUE)}\) DEFPSIST\(YES\)")
    for arm in NATIVE_HA_ARMS:
        assert pattern.search((ANSIBLE / arm).read_text()), arm


def test_bench_grant_is_separate_from_the_shared_grants() -> None:
    authz = _authz()
    assert authz["authz_bench_grants"] == [BENCH_GRANT]
    assert all(g["object"] != QUEUE for g in authz["authz_grants"])


def test_app_reply_stays_get_only_for_mqapp() -> None:
    grants = _authz()["authz_grants"]
    app_reply = [g for g in grants if (g["group"], g["object"]) == ("mqapp", "APP.REPLY")]
    assert len(app_reply) == 1
    assert "+put" not in app_reply[0]["authorities"]


def test_only_nativeha_ubuntu_loops_over_the_bench_grant() -> None:
    loopers = [p.name for p in ANSIBLE.rglob("*.yml") if "authz_bench_grants }}" in p.read_text()]
    assert loopers == ["site-nativeha-ubuntu.yml"]
    assert GRANT_LOOP in (ANSIBLE / "site-nativeha-ubuntu.yml").read_text()
