"""Guard: the Native HA authz evidence reads dmpmqaut from stdout AND stderr (#1511).

On MQ 10.0.1.0, ``dmpmqaut`` writes its authority records to stderr and exits 0 with
an empty stdout (``dspmqaut`` still uses stdout). The #943 namespace-grant evidence
asserted on ``ev_mon_ns.stdout`` only, so it failed on a queue manager that carried
the grant. This pins:

- the evidence text is built from both streams (rendered here with the playbook's own
  Jinja expression, for a 10.0.1-shaped result and an older stdout-shaped one);
- the report and the asserts use that text, never ``ev_mon_ns.stdout`` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml

PLAYBOOK = (
    Path(__file__).resolve().parents[1] / "ansible" / "site-nativeha-ubuntu-authz-validate.yml"
)
RECORD = (
    "profile:     **\nobject type: queue\nentity:      mqmon\n"
    "entity type: group\nauthority:   inq dsp"
)


def _tasks() -> list[dict[str, Any]]:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    return [task for play in plays for task in play.get("tasks", [])]


def _task(name_part: str) -> dict[str, Any]:
    matches = [t for t in _tasks() if name_part in t.get("name", "")]
    assert len(matches) == 1, f"expected one task named like {name_part!r}, got {len(matches)}"
    return matches[0]


def _render_text(result: dict[str, str]) -> str:
    expr = _task("the dmpmqaut record from stdout and stderr")["ansible.builtin.set_fact"][
        "ev_mon_ns_text"
    ]
    # The record has no HTML-special characters, so autoescape cannot alter it.
    return jinja2.Environment(autoescape=True).from_string(expr).render(ev_mon_ns=result)


@pytest.mark.parametrize(
    "result",
    [
        pytest.param({"stdout": "", "stderr": RECORD}, id="mq-10.0.1-stderr"),
        pytest.param({"stdout": RECORD, "stderr": ""}, id="stdout"),
    ],
)
def test_evidence_text_carries_the_record_from_either_stream(result: dict[str, str]) -> None:
    text = _render_text(result)
    for token in ("**", "dsp", "inq"):
        assert token in text


def test_report_and_asserts_use_the_two_stream_text() -> None:
    report = _task("report the captured authorization evidence")["ansible.builtin.debug"]["msg"]
    that = _task("assert the CHLAUTH posture")["ansible.builtin.assert"]["that"]
    assert any("ev_mon_ns_text" in line for line in report)
    ns_checks = [c for c in that if "ev_mon_ns" in c]
    assert len(ns_checks) == 3
    assert all("ev_mon_ns_text" in c and "ev_mon_ns.stdout" not in c for c in ns_checks)
