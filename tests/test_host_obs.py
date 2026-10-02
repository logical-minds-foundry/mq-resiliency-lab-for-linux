"""host-obs.yml installs nothing that is bound to a repo checkout (#1251).

Earlier observes installed two root timers that ran from whichever checkout last ran
observe, so deleting that checkout (e.g. a scratch worktree) left them failing every
tick, and one of them wrote root-owned __pycache__ into the checkout:

- lab-net-state: the polling network probe (replaced by publish-on-change, #1253);
- lab-relay-heal: the periodic Grafana relay healer (its fd leak is fixed upstream,
  vergil-project/vergil-vm#298).

host-obs.yml now only installs node_exporter, and retires both timers from hosts
that still carry them: a no-op on hosts that never had them, and errors are never
masked.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
RETIRED = ["lab-net-state", "lab-relay-heal"]
RESET_FAILED_GUARD = "_reset_failed.rc != 0 and 'not loaded' not in _reset_failed.stderr"


def _play() -> dict:
    (play,) = yaml.safe_load((ANSIBLE / "host-obs.yml").read_text(encoding="utf-8"))
    return play


def test_host_obs_installs_only_node_exporter() -> None:
    play = _play()
    assert play["roles"] == ["node-exporter"]
    for role in ("host-net-state", "host-relay-heal"):
        assert not (ANSIBLE / "roles" / role).exists(), role
    assert not (REPO_ROOT / "lab" / "scripts" / "relay-heal.sh").exists()


def test_nothing_host_obs_installs_references_a_checkout() -> None:
    # The retired timers were checkout-bound via repo_root / playbook_dir templating.
    play = _play()
    assert "repo_root" not in play.get("vars", {})
    node_exporter = ANSIBLE / "roles" / "node-exporter"
    text = "".join(p.read_text(encoding="utf-8") for p in node_exporter.rglob("*") if p.is_file())
    for needle in ("repo_root", "playbook_dir", "lab/scripts"):
        assert needle not in text, needle


def test_no_role_still_installs_a_retired_timer() -> None:
    roles_text = "".join(
        p.read_text(encoding="utf-8") for p in (ANSIBLE / "roles").rglob("*") if p.is_file()
    )
    for unit in RETIRED:
        assert unit not in roles_text, unit


def test_host_obs_retires_both_timers_without_masking_errors() -> None:
    play = _play()
    assert play["vars"]["retired_host_timers"] == RETIRED
    tasks = {t["name"]: t for t in play["tasks"]}

    find = tasks["find the retired host timer units"]
    assert find["loop"] == "{{ retired_host_timers | product(['timer', 'service']) | list }}"
    assert find["register"] == "_retired_units"

    stop = tasks["stop + disable the retired timers"]
    assert stop["ansible.builtin.systemd"] == {
        "name": "{{ item.item.0 }}.timer",
        "state": "stopped",
        "enabled": False,
    }
    assert stop["when"] == "item.item.1 == 'timer' and item.stat.exists"

    remove = tasks["remove the retired unit files"]
    assert remove["ansible.builtin.file"]["state"] == "absent"
    assert remove["when"] == "item.stat.exists", "a host that never had it is a no-op"
    reload_name = "reload systemd after retiring host timers"
    reset_name = "clear the retired services' failed state"
    assert remove["notify"] == [reload_name, reset_name]

    handlers = [h["name"] for h in play["handlers"]]
    # Handlers run in definition order: reload before reset-failed.
    assert handlers.index(reload_name) < handlers.index(reset_name)
    reset = next(h for h in play["handlers"] if h["name"] == reset_name)
    assert reset["loop"] == "{{ retired_host_timers }}"
    # Only systemd's "not loaded" (never failed) is tolerated; anything else fails loud.
    assert reset["failed_when"] == RESET_FAILED_GUARD
    assert "ignore_errors" not in reset
    for task in [*play["tasks"], *play["handlers"]]:
        assert "ignore_errors" not in task, task["name"]
