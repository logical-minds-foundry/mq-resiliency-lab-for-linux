"""Guard that per-node mqweb is retired / gated off by default (#1171, epic .github#249).

VAL-A (#1154) proved a per-node Liberty/mqweb cold-start is infeasible on the 2-vCPU
QM nodes (a node never reached `started`; another thrashed off SSH under `setmqweb`).
mqweb/REST is a non-critical, currently-unused placeholder, so every per-node mqweb
role invocation is gated on `mqweb_enabled` (default false); the replacement is a
single client-mode mqweb on the obs node (#266). These guards ensure no stack/DR play
re-introduces an *ungated* per-node mqweb, and that the default stays off — so a cold
bootstrap never pays the per-node Liberty startup cost again by accident.
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


def test_mqweb_disabled_by_default() -> None:
    """The lab default must keep per-node mqweb OFF (#1171)."""
    admin = yaml.safe_load(ADMIN_VARS.read_text(encoding="utf-8"))
    assert admin.get("mqweb_enabled") is False, (
        f"group_vars/all/admin.yml must set `mqweb_enabled: false` (#1171); "
        f"got {admin.get('mqweb_enabled')!r}"
    )
