"""Guard the acl bake/configure split on the Ubuntu boxes (#1226, epic .github#275).

`acl` (unprivileged `become_user` prereq) is baked into every Ubuntu box. The
provision-time acl tasks stay as idempotent `present` no-ops so an older box still
works, but they must NOT refresh the apt cache: `update_cache: true` forces an
`apt-get update` on every node every run even though the package is already installed.
That refresh was the 77s `acl package for unprivileged become (become_user mqm)` step on
the nativeha-ubuntu nodes (#1200 macOS run 3) — every node reported `ok`, so the whole
cost was the cache refresh, not an install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
UBUNTU_BAKES = ("bake-mq-ubuntu.yml", "bake-nativeha-ubuntu.yml", "bake-pcmk-ubuntu.yml")


def _iter_tasks(node: Any) -> Any:
    """Yield every task/block dict in a parsed playbook, descending into blocks."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _acl_apt_tasks(path: Path) -> list[dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = []
    for task in _iter_tasks(doc):
        spec = task.get("ansible.builtin.apt")
        if isinstance(spec, dict) and spec.get("name") == "acl":
            found.append(spec)
    return found


def test_every_ubuntu_bake_bakes_acl() -> None:
    """Each Ubuntu box bakes acl, so the provision-time task is a no-op on a fresh box."""
    missing = [b for b in UBUNTU_BAKES if not _acl_apt_tasks(ANSIBLE / b)]
    assert missing == [], f"these Ubuntu bakes do not install acl (#1226): {missing}"


def test_provision_acl_tasks_do_not_refresh_the_apt_cache() -> None:
    """No provision-time (non-bake) acl apt task may set `update_cache: true` (#1226)."""
    offenders = []
    checked = 0
    for pb in sorted(ANSIBLE.glob("*.yml")):
        if pb.name.startswith("bake-"):
            continue
        for spec in _acl_apt_tasks(pb):
            checked += 1
            if spec.get("update_cache") is not False:
                offenders.append(pb.name)
            if spec.get("state", "present") != "present":
                offenders.append(f"{pb.name} (state={spec.get('state')})")
    assert checked, "found no provision-time acl apt tasks; the guard is not looking at anything"
    assert offenders == [], f"acl tasks that refresh the apt cache per run (#1226): {offenders}"
