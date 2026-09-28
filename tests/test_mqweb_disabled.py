"""Guard that per-node mqweb is retired / gated off by default (#1171, epic .github#249).

VAL-A (#1154) proved a per-node Liberty/mqweb cold-start is infeasible on the 2-vCPU
QM nodes (a node never reached `started`; another thrashed off SSH under `setmqweb`).
mqweb/REST is a non-critical, currently-unused placeholder, so every per-node mqweb
role invocation is gated on `mqweb_enabled` (default false); the replacement is a
single client-mode mqweb on the obs node (#266). These guards ensure no stack/DR play
re-introduces an *ungated* per-node mqweb, and that the default stays off — so a cold
bootstrap never pays the per-node Liberty startup cost again by accident.

Two invocation shapes must both stay gated: the stack/DR plays' top-level `roles:`
entries (#1171), AND `include_role`/`import_role` mqweb tasks inside a role — the
`mq-qmgr` role enabled mqweb via an ungated `include_role: name: mqweb` that #1171
missed, so svc-sim's SVCQM still paid the per-node Liberty tax until it was gated too
(#1188). Both prongs are scanned here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
ADMIN_VARS = ANSIBLE / "group_vars" / "all" / "admin.yml"


def _playbooks() -> list[Path]:
    """All top-level playbooks (site-*.yml and the _*.yml play fragments)."""
    files = sorted(ANSIBLE.glob("*.yml"))
    assert files, f"no playbooks found under {ANSIBLE}"
    return files


def _mqweb_role_includes(path: Path) -> list[Any]:
    """Every `roles:` entry that pulls in the mqweb role, across all plays in a file."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, list):
        return []
    hits: list[Any] = []
    for play in doc:
        if not isinstance(play, dict):
            continue
        for entry in play.get("roles", []) or []:
            if entry == "mqweb" or (isinstance(entry, dict) and entry.get("role") == "mqweb"):
                hits.append(entry)
    return hits


def test_every_mqweb_role_include_is_gated_on_mqweb_enabled() -> None:
    """No playbook may include the mqweb role ungated — each include must carry a
    `when:` referencing `mqweb_enabled` (#1171). A bare `- mqweb` string is ungated
    and fails."""
    seen = 0
    for pb in _playbooks():
        for entry in _mqweb_role_includes(pb):
            seen += 1
            assert isinstance(entry, dict), (
                f"{pb.name}: mqweb role is included ungated (bare string) — it must be "
                f"`- role: mqweb` with `when: mqweb_enabled | default(false)` (#1171)"
            )
            when = str(entry.get("when", ""))
            assert "mqweb_enabled" in when, (
                f"{pb.name}: mqweb role include is not gated on mqweb_enabled (#1171); "
                f"when={entry.get('when')!r}"
            )
    assert seen >= 8, (
        f"expected the 8 stack/DR mqweb plays to still carry (gated) mqweb includes; found {seen}"
    )


def _iter_tasks(node: Any) -> Any:
    """Recursively yield every task dict in a parsed playbook/role-task document,
    descending into block/rescue/always so a nested include is never missed."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _mqweb_role_task_includes() -> list[tuple[Path, dict[str, Any]]]:
    """Every `include_role`/`import_role` task pulling in the mqweb role, across all
    role task files (ansible/roles/*/tasks/*.yml) and top-level playbooks."""
    hits: list[tuple[Path, dict[str, Any]]] = []
    candidates = sorted((ANSIBLE / "roles").rglob("tasks/*.yml")) + _playbooks()
    for path in candidates:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for task in _iter_tasks(doc):
            for verb in (
                "ansible.builtin.include_role",
                "include_role",
                "ansible.builtin.import_role",
                "import_role",
            ):
                spec = task.get(verb)
                if isinstance(spec, dict) and spec.get("name") == "mqweb":
                    hits.append((path, task))
    return hits


def test_every_mqweb_include_role_task_is_gated_on_mqweb_enabled() -> None:
    """No role/playbook task may `include_role`/`import_role` mqweb ungated — each must
    carry a `when:` referencing `mqweb_enabled` (#1188). The `mq-qmgr` role's ungated
    include let svc-sim's SVCQM run a per-node mqweb; this guards that path and any future
    one."""
    hits = _mqweb_role_task_includes()
    assert hits, "expected at least the mq-qmgr role's mqweb include_role task to be present"
    for path, task in hits:
        when = str(task.get("when", ""))
        assert "mqweb_enabled" in when, (
            f"{path.relative_to(REPO_ROOT)}: mqweb include_role/import_role task is not "
            f"gated on mqweb_enabled (#1188); when={task.get('when')!r}"
        )


def test_mqweb_disabled_by_default() -> None:
    """The lab default must keep per-node mqweb OFF (#1171)."""
    admin = yaml.safe_load(ADMIN_VARS.read_text(encoding="utf-8"))
    assert admin.get("mqweb_enabled") is False, (
        f"group_vars/all/admin.yml must set `mqweb_enabled: false` (#1171); "
        f"got {admin.get('mqweb_enabled')!r}"
    )
