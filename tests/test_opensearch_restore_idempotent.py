"""Guard the idempotent restore-on-bring-up in the opensearch role (#1379).

site-obs.yml runs on every stack bootstrap while the commons (obs) stay up, so the
restore must not re-POST a snapshot whose indices the cluster already holds: that is an
HTTP 500 ("an open index with same name already exists") and it failed the second
stack's observe phase. Structural tests over ``configure.yml`` plus a render of the
missing-index computation through Ansible's own templar. The live proof is a second-stack
bootstrap with the commons up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = REPO_ROOT / "ansible" / "roles" / "opensearch" / "tasks" / "configure.yml"

LIST_EXISTING = "list the indices the cluster already holds (open or closed)"
COMPUTE = "compute the snapshot indices missing from the cluster"
RESTORE = "restore the latest repo snapshot (only the indices not already present)"
SKIP_REPORT = "report when every snapshot index is already present (restore skipped)"
SUPPRESSORS = ("ignore_errors", "failed_when", "ignore_unreachable")


def _flat(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for task in tasks:
        out.append(task)
        for key in ("block", "rescue", "always"):
            out += _flat(task.get(key, []))
    return out


def _by_name() -> dict[str, dict[str, Any]]:
    tasks = yaml.safe_load(CONFIGURE.read_text(encoding="utf-8"))
    return {t["name"]: t for t in _flat(tasks) if "name" in t}


def _restore_block() -> dict[str, Any]:
    tasks = yaml.safe_load(CONFIGURE.read_text(encoding="utf-8"))
    return next(
        t for t in tasks if t.get("name") == "restore the latest snapshot from the staged repo"
    )


def _whens(task: dict[str, Any]) -> list[str]:
    when = task.get("when", [])
    return [when] if isinstance(when, str) else list(when)


def _missing(snapshots: list[dict[str, Any]], restore_id: str, existing: list[str]) -> Any:
    expr = _by_name()[COMPUTE]["ansible.builtin.set_fact"]["opensearch_restore_indices"]
    variables = {
        "opensearch_repo_snapshots": {"json": {"snapshots": snapshots}},
        "opensearch_restore_id": restore_id,
        "opensearch_existing_indices": {"json": [{"index": i} for i in existing]},
    }
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr.strip()))


SNAPS = [
    {"snapshot": "snap-20260806t120000z", "indices": ["logs-2026.08.06"]},
    {"snapshot": "snap-20260807t153535z", "indices": ["logs-2026.08.07", "logs-2026.08.06"]},
]


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        # Fresh cold obs: nothing present, so the first restore restores everything.
        ([], ["logs-2026.08.06", "logs-2026.08.07"]),
        # Second stack bootstrap: an earlier bring-up already restored them all.
        (["logs-2026.08.06", "logs-2026.08.07", ".kibana_1"], []),
        # Partial: only the absent index is restored, the live one is left alone.
        (["logs-2026.08.07"], ["logs-2026.08.06"]),
    ],
)
def test_restore_set_is_snapshot_indices_minus_existing(
    existing: list[str], expected: list[str]
) -> None:
    assert list(_missing(SNAPS, "snap-20260807t153535z", existing)) == expected


def test_existing_index_listing_covers_open_closed_and_hidden() -> None:
    task = _by_name()[LIST_EXISTING]
    uri = task["ansible.builtin.uri"]
    assert uri["method"] == "GET"
    assert "/_cat/indices?" in uri["url"]
    assert "format=json" in uri["url"]
    assert "expand_wildcards=all" in uri["url"], "closed indices must count as present"
    assert task["register"] == "opensearch_existing_indices"


def test_restore_runs_after_the_existence_check_and_is_guarded_by_it() -> None:
    names = [t.get("name") for t in _restore_block()["block"]]
    assert names.index(LIST_EXISTING) < names.index(COMPUTE) < names.index(RESTORE)
    restore = _by_name()[RESTORE]
    assert "opensearch_restore_indices | length > 0" in _whens(restore)
    body = restore["ansible.builtin.uri"]["body"]
    assert body["indices"] == "{{ opensearch_restore_indices | join(',') }}"
    assert body["include_global_state"] is False
    assert "_restore?wait_for_completion=true" in restore["ansible.builtin.uri"]["url"]
    assert restore["ansible.builtin.uri"]["status_code"] == 200


def test_skipped_restore_is_logged_not_silent() -> None:
    report = _by_name()[SKIP_REPORT]
    assert "ansible.builtin.debug" in report
    assert "opensearch_restore_indices | length == 0" in _whens(report)


def test_no_unscoped_full_restore_remains() -> None:
    restores = [
        t
        for t in _flat(yaml.safe_load(CONFIGURE.read_text(encoding="utf-8")))
        if "_restore" in str(t.get("ansible.builtin.uri", {}).get("url", ""))
    ]
    assert [t["name"] for t in restores] == [RESTORE]


def test_restore_path_has_no_error_suppression() -> None:
    block = _restore_block()
    for task in [block, *_flat(block["block"])]:
        hits = [k for k in SUPPRESSORS if k in task]
        assert not hits, f"{task.get('name')!r} suppresses errors via {hits} (#1379 fail-loud)"
