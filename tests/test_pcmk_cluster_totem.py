"""pcmk-cluster corosync timing variables (#1621).

The pcmk-drbd-ubuntu arm runs corosync with the RDQM blueprint's `token 10000` and its
link-0 knet ping values (docs/reference/rdqm-blueprint/rdqm-*/corosync-conf.txt, ledger
row CL-1). The variables default to "not set", and with the defaults the rendered
`pcs cluster setup` script must be byte-identical to the one before #1621, so the
pcmk-ubuntu arm (both sites) is unchanged (plan Global Constraint).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "pcmk-cluster"
TASKS = ROLE / "tasks" / "main.yml"
DEFAULTS = ROLE / "defaults" / "main.yml"
PDRBD_COROSYNC = ANSIBLE / "vars" / "pdrbd-corosync.yml"
BLUEPRINT = REPO_ROOT / "docs" / "reference" / "rdqm-blueprint"

# sha256 of the auth + cluster setup script rendered for each pcmk-ubuntu site, taken on
# develop at 8a8c2d0 (before #1621). With the new variables at their defaults the render
# must not move.
_PCMK_RENDER_SHA256 = {
    "_pcmk-cluster-ha.yml": "809c1229d97998675cbfdd43424bbf2e6e9eb20f46218dbe16320cb1d18d6f6b",
    "_pcmk-dr-replication.yml": "bdcfbe24965d6e0ac7d6660ba8f47f33b6e7312b71d3b68b2d6858d1774722a0",
}
_TIMING_VARS = (
    "pcmk_cluster_totem_token_ms",
    "pcmk_cluster_knet_ping_timeout_ms",
    "pcmk_cluster_knet_ping_interval_ms",
    "pcmk_cluster_knet_pong_count",
)


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _setup_script() -> str:
    for task in _load(TASKS):
        if "auth + cluster setup" in (task.get("name") or ""):
            (inner,) = task["block"]
            return str(inner["ansible.builtin.shell"])
    raise AssertionError("no pcmk-cluster 'auth + cluster setup' block")


def _cluster_play(playbook: str) -> dict[str, Any]:
    plays = [p for p in _load(ANSIBLE / playbook) if isinstance(p, dict)]
    (play,) = [p for p in plays if "pcmk-cluster" in (p.get("roles") or [])]
    return play


def _play_vars(playbook: str) -> dict[str, Any]:
    """Role defaults, then the play's vars_files, then its vars (Ansible precedence)."""
    play = _cluster_play(playbook)
    merged: dict[str, Any] = dict(_load(DEFAULTS))
    for vf in play.get("vars_files") or []:
        merged.update(_load(ANSIBLE / vf))
    merged.update(play.get("vars") or {})
    return merged


def _render(playbook: str) -> str:
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        autoescape=jinja2.select_autoescape(
            enabled_extensions=(), default_for_string=False, default=False
        ),
    )
    return env.from_string(_setup_script()).render(**_play_vars(playbook))


def _blueprint_values(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    keys = ("token", "knet_ping_timeout", "knet_ping_interval", "knet_pong_count")
    out = {}
    for key in keys:
        m = re.search(rf"^\s*{key}:\s*(\d+)\s*$", text, re.MULTILINE)
        assert m, f"{path} has no {key}"
        out[key] = int(m.group(1))
    return out


def test_defaults_leave_every_timing_value_unset() -> None:
    d = _load(DEFAULTS)
    assert {k: d[k] for k in _TIMING_VARS} == dict.fromkeys(_TIMING_VARS, 0)


def test_pcmk_ubuntu_setup_render_is_byte_identical() -> None:
    for playbook, want in _PCMK_RENDER_SHA256.items():
        play = _cluster_play(playbook)
        assert not play.get("vars_files"), f"{playbook} must not pick up timing values"
        assert not set(_TIMING_VARS) & set(play.get("vars") or {}), playbook
        got = hashlib.sha256(_render(playbook).encode()).hexdigest()
        assert got == want, f"{playbook}: the default render changed (#1621)"


def test_pdrbd_values_are_the_blueprints_on_every_rdqm_node() -> None:
    mine = _load(PDRBD_COROSYNC)
    want = {
        "token": mine["pcmk_cluster_totem_token_ms"],
        "knet_ping_timeout": mine["pcmk_cluster_knet_ping_timeout_ms"],
        "knet_ping_interval": mine["pcmk_cluster_knet_ping_interval_ms"],
        "knet_pong_count": mine["pcmk_cluster_knet_pong_count"],
    }
    assert want["token"] == 10000
    confs = sorted(BLUEPRINT.glob("rdqm-*/corosync-conf.txt"))
    assert len(confs) == 6, confs
    for conf in confs:
        assert _blueprint_values(conf) == want, conf
    # Not set by the blueprint, so not set here: corosync derives both from the token.
    for derived in ("token_coefficient", "consensus"):
        assert all(derived not in c.read_text(encoding="utf-8") for c in confs)
    assert set(mine) == set(_TIMING_VARS)


def test_both_pdrbd_sites_render_token_10000_and_the_knet_link() -> None:
    for playbook in ("_pdrbd-cluster-ha.yml", "_pdrbd-dr-replication.yml"):
        assert _cluster_play(playbook)["vars_files"] == ["vars/pdrbd-corosync.yml"]
        setup = next(line for line in _render(playbook).splitlines() if "pcs cluster setup" in line)
        assert setup.endswith(
            " transport knet link linknumber=0 ping_timeout=5000 ping_interval=500"
            " pong_count=2 totem token=10000 --force"
        ), setup
        # No crypto options: pcs keeps its knet default (aes256/sha256), the owner's
        # SC-2 decision (stricter than RDQM's none).
        assert "crypto" not in setup


def test_knet_values_are_validated_all_or_none() -> None:
    tasks = _load(TASKS)
    (check,) = [t for t in tasks if "corosync timing variables are consistent" in t["name"]]
    that = " ".join(check["ansible.builtin.assert"]["that"])
    assert "length in [0, 3]" in that
    names = [t.get("name", "") for t in tasks]
    assert names.index(check["name"]) < next(
        i for i, n in enumerate(names) if "auth + cluster setup" in n
    )


def test_running_values_are_checked_on_every_node_and_fail_loud() -> None:
    tasks = _load(TASKS)
    names = [t.get("name", "") for t in tasks]
    read_i = names.index("read the running corosync totem settings")
    check_i = names.index("corosync runs the configured timing values (fail loud)")
    online_i = names.index("wait for all nodes online")
    assert online_i < read_i < check_i
    read, check = tasks[read_i], tasks[check_i]
    assert read["ansible.builtin.command"]["argv"] == ["corosync-cmapctl", "totem."]
    assert read["changed_when"] is False
    assert "pcmk_cluster_totem_token_ms" in read["when"]
    for task in (read, check):
        assert "run_once" not in task, "every node's corosync is checked"
        assert "ignore_errors" not in task and "failed_when" not in task
    loop = check["loop"]
    for key in (
        "totem.token",
        "totem.interface.0.knet_ping_timeout",
        "totem.interface.0.knet_ping_interval",
        "totem.interface.0.knet_pong_count",
    ):
        assert f"'{key}'" in loop, key
