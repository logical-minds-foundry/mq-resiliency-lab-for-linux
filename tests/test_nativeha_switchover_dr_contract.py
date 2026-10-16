"""Guard: the Native HA switchover playbooks accept the `mqlab dr` verb contract (#1571).

`mqlab dr cutover|failback` runs a stack's playbook DR verb as
``ansible-playbook <pb> -e dr_direction=<a2b|b2a> -e qm_name=<QM> -e rpo0_drill=<bool>``
(src/mqlab/cli.py, #1424). Both Native HA stacks declare their CRR switchover playbook as
that verb, which used to read only ``-e target_live=a|b``. These tests pin the shared
resolution (a2b -> site B Live, b2a -> site A Live, a direct ``target_live`` still wins),
the fail-loud preflight that runs before any node is touched, and the QM precedence, by
evaluating the real expressions with Ansible's own templar and by running the playbooks'
localhost preflight with ``ansible-playbook`` itself.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from mqlab.cli import _dr_command
from mqlab.stacks import lab_stacks

ANSIBLE = Path(__file__).resolve().parents[1] / "ansible"
VARS = ANSIBLE / "vars" / "nativeha-switchover.yml"
PREFLIGHT = ANSIBLE / "tasks" / "nativeha-switchover-target.yml"
# stack -> (playbook, its site-A group, its site-B group)
ARMS = {
    "nativeha-rhel-crr": ("site-nativeha-switchover.yml", "nha_rhel_crr_a", "nha_rhel_crr_b"),
    "nativeha-ubuntu": ("site-nativeha-ubuntu-switchover.yml", "nha_ubuntu_a", "nha_ubuntu_b"),
}
DEFAULT_QM = {"nativeha-rhel-crr": "NHARCAPP", "nativeha-ubuntu": "NHAUAPP"}


def _plays(stack: str) -> list[dict[str, Any]]:
    return yaml.safe_load((ANSIBLE / ARMS[stack][0]).read_text())


def _play(stack: str, hosts: str) -> dict[str, Any]:
    hits = [p for p in _plays(stack) if p["hosts"] == hosts]
    assert len(hits) == 1, f"expected one play on {hosts} in {ARMS[stack][0]}"
    return hits[0]


def _render(expr: str, extra: dict[str, Any], play_vars: dict[str, Any] | None = None) -> Any:
    # The play's own vars and the shared vars file, as templates, then the extra-vars on
    # top (extra-vars outrank everything, as in Ansible's precedence).
    variables: dict[str, Any] = {}
    for source in (yaml.safe_load(VARS.read_text()), play_vars or {}):
        for key, value in source.items():
            variables[key] = trust_as_template(value) if isinstance(value, str) else value
    variables.update(extra)
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _asserts() -> list[dict[str, Any]]:
    tasks = yaml.safe_load(PREFLIGHT.read_text())
    return [t["ansible.builtin.assert"] for t in tasks]


def _preflight_passes(extra: dict[str, Any]) -> bool:
    # Ansible's assert evaluates each `that` in order and fails on the first false one.
    for spec in _asserts():
        for cond in spec["that"]:
            if not _render("{{ " + cond.strip() + " }}", extra):
                return False
    return True


# --- the mapping ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "live"),
    [
        ({"dr_direction": "a2b"}, "b"),  # mqlab dr cutover
        ({"dr_direction": "b2a"}, "a"),  # mqlab dr failback
        ({"target_live": "b"}, "b"),  # by hand, cutover
        ({"target_live": "a"}, "a"),  # by hand, failback
        ({"dr_direction": "a2b", "target_live": "b"}, "b"),  # both, agreeing
    ],
)
def test_the_live_site_resolves_from_either_input(extra: dict[str, str], live: str) -> None:
    assert _render("{{ nha_switchover_live }}", extra) == live
    assert _preflight_passes({**extra, "rpo0_drill": "false"})


@pytest.mark.parametrize("stack", sorted(ARMS))
@pytest.mark.parametrize(("direction", "live"), [("a2b", "b"), ("b2a", "a")])
def test_each_sites_group_role_follows_the_direction(stack: str, direction: str, live: str) -> None:
    _, site_a, site_b = ARMS[stack]
    extra = {"dr_direction": direction, "qm_name": DEFAULT_QM[stack], "rpo0_drill": "false"}
    for site, group in (("a", site_a), ("b", site_b)):
        play = _play(stack, group)
        assert "vars/nativeha-switchover.yml" in play["vars_files"]
        role = _render(play["vars"]["this_role"], extra, play["vars"])
        assert role == ("live" if site == live else "Recovery")


@pytest.mark.parametrize("stack", sorted(ARMS))
def test_the_host_plays_read_only_the_resolved_target(stack: str) -> None:
    # A raw target_live in a host play would bypass the resolution (and break mqlab dr).
    for play in _plays(stack)[1:]:
        assert "target_live" not in yaml.safe_dump(play.get("vars", {}))


# --- the fail-loud preflight ------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {},  # neither input
        {"dr_direction": "a2b", "target_live": "a"},  # both, disagreeing
        {"dr_direction": "b2a", "target_live": "b"},  # both, disagreeing
        {"dr_direction": "sideways"},  # unknown direction
        {"target_live": "c"},  # unknown site
        {"target_live": ""},  # `-e target_live=` with no value
        {"dr_direction": "a2b", "rpo0_drill": "true"},  # a drill this playbook cannot do
    ],
)
def test_the_preflight_refuses_bad_input(extra: dict[str, str]) -> None:
    assert not _preflight_passes(extra)


@pytest.mark.parametrize("stack", sorted(ARMS))
def test_the_preflight_is_the_first_play_and_runs_on_the_controller(stack: str) -> None:
    first = _plays(stack)[0]
    assert first["hosts"] == "localhost"
    assert first["connection"] == "local"
    assert first["gather_facts"] is False
    assert first["vars_files"] == ["vars/nativeha-switchover.yml"]
    assert first["tasks"] == [
        {"ansible.builtin.import_tasks": "tasks/nativeha-switchover-target.yml"}
    ]


def test_every_preflight_assert_says_why() -> None:
    for spec in _asserts():
        assert spec["fail_msg"].strip()


# --- the QM --------------------------------------------------------------------------


@pytest.mark.parametrize("stack", sorted(ARMS))
@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, None),  # the playbook's default
        ({"qm_app": "QMAPPX"}, "QMAPPX"),
        ({"qm_name": "QMNAMEX"}, "QMNAMEX"),  # what mqlab passes
        ({"qm_name": "QMNAMEX", "qm_app": "QMAPPX"}, "QMNAMEX"),
    ],
)
def test_the_qm_is_qm_name_then_qm_app_then_the_default(
    stack: str, extra: dict[str, str], expected: str | None
) -> None:
    want = expected or DEFAULT_QM[stack]
    for play in _plays(stack)[1:]:
        play_vars = play["vars"]
        assert _render("{{ qm_name }}", extra, play_vars) == want
        if "ks_cn" in play_vars:
            assert _render("{{ ks_cn }}", extra, play_vars) == want


@pytest.mark.parametrize("stack", sorted(ARMS))
def test_mqlab_dr_passes_the_contract_these_playbooks_read(stack: str) -> None:
    # The stack's declared verbs name this playbook, and mqlab passes exactly the vars the
    # preflight and resolution read — so a contract change on either side fails here.
    s = lab_stacks()[stack]
    for verb in ("dr-cutover", "dr-failback"):
        assert s.verbs[verb] == {"playbook": ARMS[stack][0]}
    argv = _dr_command(s, "playbook", ARMS[stack][0], "a2b", rpo0_drill=False).argv
    assert argv[2::2] == ["-e", "-e", "-e"]
    extras: dict[str, str] = {}
    for arg in argv[3::2]:
        key, _, value = arg.partition("=")
        extras[key] = value
    assert extras == {"dr_direction": "a2b", "qm_name": s.qm.qm_app, "rpo0_drill": "false"}
    assert _render("{{ nha_switchover_live }}", extras) == "b"
    assert _preflight_passes(extras)


# --- the real thing: ansible-playbook runs the preflight ----------------------------


def _ansible_playbook(
    stack: str, extra: dict[str, str], home: Path
) -> subprocess.CompletedProcess[str]:
    # Run from outside ansible/ (no ansible.cfg) against a localhost-only inventory: the
    # preflight play runs, and every node play matches no host and is skipped.
    argv = [str(Path(sys.executable).parent / "ansible-playbook"), "-i", "localhost,"]
    argv += [str(ANSIBLE / ARMS[stack][0])]
    for key, value in extra.items():
        argv += ["-e", f"{key}={value}"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update({"ANSIBLE_HOME": str(home), "ANSIBLE_LOCAL_TEMP": str(home / "tmp")})
    env.update({"LC_ALL": "C.UTF-8", "ANSIBLE_NOCOLOR": "1"})
    return subprocess.run(argv, cwd=home, env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("stack", sorted(ARMS))
@pytest.mark.parametrize(
    "extra",
    [
        {"dr_direction": "b2a", "qm_name": "QMX", "rpo0_drill": "false"},  # mqlab dr
        {"target_live": "b", "qm_app": "QMX"},  # by hand
    ],
)
def test_ansible_playbook_passes_the_preflight_and_reaches_the_node_plays(
    stack: str, extra: dict[str, str], tmp_path: Path
) -> None:
    run = _ansible_playbook(stack, extra, tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert re.search(r"localhost\s+: ok=3\s+changed=0\s+unreachable=0\s+failed=0", run.stdout)
    assert f"PLAY [{ARMS[stack][1]}]" in run.stdout  # the node plays ran (matching no host)


@pytest.mark.parametrize("stack", sorted(ARMS))
@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ({}, "the switchover needs its target"),
        ({"dr_direction": "a2b", "target_live": "a"}, "target_live=a disagree"),
        ({"dr_direction": "a2b", "rpo0_drill": "true"}, "does not implement the RPO-0"),
    ],
)
def test_ansible_playbook_fails_loud_before_any_node_play(
    stack: str, extra: dict[str, str], why: str, tmp_path: Path
) -> None:
    run = _ansible_playbook(stack, extra, tmp_path)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert why in out, out
    assert f"PLAY [{ARMS[stack][1]}]" not in run.stdout
