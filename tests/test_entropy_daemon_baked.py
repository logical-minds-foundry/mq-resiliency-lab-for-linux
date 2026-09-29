"""Guard that an entropy daemon is baked into EVERY box (#1190, epics .github#267/#249).

Root cause (systematic-debugging of VAL #1177): on a headless nested-virt VM with no
hardware RNG and no entropy daemon, Java's SecureRandom (the OpenSearch JDK ships
`securerandom.source=file:/dev/random` + `NativePRNGBlocking`) blocks during JVM
cold-start — `bin/opensearch` wedges in its `TempDirectory` helper before the node JVM
starts, so OpenSearch never binds :9200 and the observe phase never completes. Installing
+ enabling an entropy daemon (haveged on Debian, rng-tools/rngd on RedHat) keeps the
kernel pool full so the block never happens.

Because the launcher helpers are invoked with minimal args and never read `jvm.options`,
this fix must live at the OS layer and be baked into every box (Liberty/mqweb is Java too,
so it is not OpenSearch-specific). These guards fail loudly if a bake ever ships without
the entropy role, or if the role stops actually installing + enabling a daemon.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ENTROPY_ROLE = ANSIBLE / "roles" / "entropy"


def _bakes() -> list[Path]:
    files = sorted(ANSIBLE.glob("bake-*.yml"))
    assert files, f"no bake playbooks found under {ANSIBLE}"
    return files


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


def _includes_role(path: Path, role: str) -> bool:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for task in _iter_tasks(doc):
        for verb in (
            "ansible.builtin.include_role",
            "include_role",
            "ansible.builtin.import_role",
            "import_role",
        ):
            spec = task.get(verb)
            if isinstance(spec, dict) and spec.get("name") == role:
                return True
    return False


def test_every_bake_includes_the_entropy_role() -> None:
    """No box may bake without the entropy daemon (#1190). A JVM box that ships without it
    hangs OpenSearch/Liberty cold-start on nested-virt when the RNG pool is empty."""
    missing = [pb.name for pb in _bakes() if not _includes_role(pb, "entropy")]
    assert missing == [], f"these bakes are missing the entropy role include (#1190): {missing}"


def test_entropy_role_installs_and_enables_a_daemon() -> None:
    """The entropy role must actually install a package AND enable+start its service —
    a role that only installs (never starts) leaves the pool unfed at boot."""
    tasks = yaml.safe_load((ENTROPY_ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
    installs = any(
        ("ansible.builtin.package" in t or "ansible.builtin.apt" in t or "package" in t)
        for t in _iter_tasks(tasks)
        if isinstance(t, dict)
    )
    svc = next(
        (
            t["ansible.builtin.systemd"]
            for t in _iter_tasks(tasks)
            if isinstance(t, dict) and "ansible.builtin.systemd" in t
        ),
        None,
    )
    assert installs, "entropy role must install the daemon package"
    assert svc is not None, "entropy role must manage the daemon via ansible.builtin.systemd"
    assert svc.get("state") == "started", "entropy daemon must be started at bake"
    assert svc.get("enabled") is True, "entropy daemon must be enabled (survive reboot) at bake"


def test_entropy_daemon_is_distro_selected() -> None:
    """haveged on Debian, rng-tools on RedHat — a single hardcoded package would break the
    RHEL bake (haveged is EPEL-only there); rng-tools is base-repo on both families."""
    defaults = yaml.safe_load((ENTROPY_ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
    pkg = defaults.get("entropy_pkg", "")
    svc = defaults.get("entropy_service", "")
    assert "haveged" in pkg and "rng-tools" in pkg, (
        f"entropy_pkg must select haveged (Debian) / rng-tools (RedHat); got {pkg!r}"
    )
    assert "haveged" in svc and "rngd" in svc, (
        f"entropy_service must select haveged (Debian) / rngd (RedHat); got {svc!r}"
    )
