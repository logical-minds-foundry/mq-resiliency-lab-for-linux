"""site-pdrbd-dr-switch.yml + the DR-sync wait (epic .github#310 T6, #1428).

RDQM-style DR demote/promote of the DR clone only; the HA layer at both sites is never
touched. Pinned statically, plus the two text parses the switch and the role depend on,
run against real captured output: the `crm_mon -1 -r` clone-role parse (this arm's live
format from #1615 and RDQM's blueprint) and the `drbdsetup status --verbose` parse (#1615).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
PB = ANSIBLE / "site-pdrbd-dr-switch.yml"
SITE = ANSIBLE / "site-pdrbd.yml"
SEED = ANSIBLE / "tasks" / "rpo0-drill-seed.yml"
VERIFY = ANSIBLE / "tasks" / "rpo0-drill-verify.yml"
_SRC_REL = "tasks/pdrbd-dr-switch-source.yml"
SRC_TASKS = ANSIBLE / _SRC_REL
CRM_VARS = ANSIBLE / "vars" / "pdrbd-crm-mon.yml"
BLUEPRINT = REPO_ROOT / "docs" / "reference" / "rdqm-blueprint"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_tasks() -> list[tuple[str, dict[str, Any]]]:
    """Every task in run order, with the source include expanded in place."""
    out: list[tuple[str, dict[str, Any]]] = []
    for play in _load(PB):
        for t in play.get("tasks", []):
            is_source = t.get("ansible.builtin.include_tasks") == _SRC_REL
            inner = _source_tasks() if is_source else t.get("block", [t])
            out.extend((play["name"], i) for i in inner)
    return out


def _switch_top_level() -> list[dict[str, Any]]:
    [play] = [p for p in _load(PB) if p["name"] == "switch"]
    return play["tasks"]


def _source_include() -> dict[str, Any]:
    [inc] = [t for t in _switch_top_level() if t.get("ansible.builtin.include_tasks") == _SRC_REL]
    return inc


def _source_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = _load(SRC_TASKS)
    return tasks


def _index(fragment: str) -> int:
    return next(i for i, (_, t) in enumerate(_all_tasks()) if fragment in t["name"])


def _task(fragment: str) -> dict[str, Any]:
    return _all_tasks()[_index(fragment)][1]


def _switch_vars() -> dict[str, Any]:
    [play] = [p for p in _load(PB) if p["name"] == "switch"]
    return play["vars"]


# --- ordering and gates ---------------------------------------------------------------------


def test_dr_switch_validates_direction() -> None:
    assert _index("assert dr_direction") == 0
    that = str(_task("assert dr_direction")["ansible.builtin.assert"]["that"])
    assert "['a2b', 'b2a']" in that
    assert "qm_name" in that


_PREFLIGHT = "pre-flight — assert the target's disk is UpToDate"
_REASSERT = "re-assert the target's disk is UpToDate"


def test_dr_switch_asserts_disk_state_before_promote() -> None:
    # Review Focus 4: the gate runs before ANY change and again right before the promote.
    assert _index(_PREFLIGHT) < _index(_REASSERT) < _index("promote at the target site")
    assert _index("target DR state after the source demote") < _index(_REASSERT)


def test_dr_switch_preflight_gate_precedes_every_change() -> None:
    # A refused switch leaves the source untouched and still serving: the pre-flight gate
    # comes before the RPO-0 seed, the demote and every other changing task.
    gate = _index(_PREFLIGHT)
    changing = [
        i
        for i, (_, t) in enumerate(_all_tasks())
        if t.get("changed_when") is True or "include_tasks" in str(t)
    ]
    assert changing
    assert all(gate < i for i in changing), changing
    assert gate < _index("stop and demote at the source site")
    assert gate < _index("RPO-0 seed")
    msg = _task(_PREFLIGHT)["ansible.builtin.assert"]["fail_msg"]
    assert "replication is broken or lagging" in msg
    assert "NOTHING WAS CHANGED" in msg


def test_dr_switch_force_is_explicit() -> None:
    # Both gates evaluate the one shared expression.
    for frag in (_PREFLIGHT, _REASSERT):
        assert _task(frag)["ansible.builtin.assert"]["that"] == "dst_gate | bool", frag
    gate = _switch_vars()["dst_gate"]
    assert "dr_force_b" in gate
    # A planned switch needs the link up and the source's disk UpToDate too: a
    # disconnected target can call itself UpToDate while missing protocol-A writes.
    assert "dst_conn == ['Connected']" in gate
    assert "dst_peer_disk == ['UpToDate']" in gate
    assert _switch_vars()["dr_force_b"] == "{{ dr_force | default(false) | bool }}"


def test_late_refusal_says_the_source_was_demoted_and_how_to_repromote() -> None:
    msg = _task(_REASSERT)["ansible.builtin.assert"]["fail_msg"]
    assert "THE SOURCE" in msg
    assert "WAS DEMOTED" in msg
    assert "{{ src_repromote_cmd }}" in msg
    cmd = _switch_vars()["src_repromote_cmd"]
    # The exact inverse of the demote, on the node that ran it.
    assert "ansible '{{ groups[src][0] }}' -b -m shell" in cmd
    assert "drbd_dr_qm-clone target-role=Promoted" in cmd
    assert "-dr-role --update 1" in cmd
    demote = _task("stop and demote at the source site")
    assert demote["delegate_to"] == "{{ groups[src][0] }}"


def test_forced_cutover_refuses_the_rpo0_drill() -> None:
    # Its own assert, second only to the direction check, with a message that names the
    # conflict alone (the combined message read "must be a2b or b2a (got a2b)" live, #1428).
    assert _index("assert no RPO-0 drill on a forced cutover") == 1
    gate = _task("assert no RPO-0 drill on a forced cutover")["ansible.builtin.assert"]
    assert "dr_force" in str(gate["that"])
    assert "rpo0_drill" in str(gate["that"])
    assert "dr_direction" not in gate["fail_msg"]


def test_source_steps_are_skipped_only_on_a_forced_cutover() -> None:
    # An include, not a block (#1647): a block's tasks have their delegate_to templated
    # before their inherited `when`; a skipped include never loads its file.
    inc = _source_include()
    assert inc["when"] == "not dr_force_b"
    assert not [t["name"] for t in _switch_top_level() if "block" in t]
    names = [t["name"] for t in _source_tasks()]
    for frag in (
        "source site crm_mon",
        "find the source site's DR-promoted node",
        "assert the source site's DR clone is Promoted",
        "RPO-0 seed",
        "stop and demote at the source site",
        "assert the source DR resource is Secondary",
    ):
        assert any(frag in n for n in names), frag
    for frag in ("target site crm_mon", "promote at the target site", _PREFLIGHT, _REASSERT):
        assert "when" not in _task(frag), frag


def _delegates_to_source(task: dict[str, Any]) -> bool:
    target = str(task.get("delegate_to", ""))
    return "src" in target or "rpo0-drill-seed" in str(task)


def test_forced_cutover_never_contacts_an_unreachable_source() -> None:
    # Drill 6 destroys every site-A node, then force-promotes B. The source may be
    # unreachable, so nothing on the forced path may connect to it:
    # - no play targets a node directly (all run on localhost, no fact gathering), so
    #   Ansible never opens a connection it was not asked for;
    for play in _load(PB):
        assert play["hosts"] == "localhost", play["name"]
        assert play["gather_facts"] is False, play["name"]
    # - every task that reaches a source node is in the ONE task file included under
    #   `not dr_force_b` (a skipped include is never loaded or templated), and no other task;
    inside = _source_tasks()
    outside = [t for t in _switch_top_level() if t.get("ansible.builtin.include_tasks") != _SRC_REL]
    assert len(outside) == len(_switch_top_level()) - 1
    assert all(_delegates_to_source(t) or "set_fact" in str(t) or "assert" in str(t)
               for t in inside)  # fmt: skip
    assert not [t["name"] for t in outside if _delegates_to_source(t)]
    # - the target-side tasks reach only the target (its first node, or its DR member).
    for t in outside:
        target = str(t.get("delegate_to", ""))
        assert target in ("", "{{ groups[dst][0] }}", "{{ dr_dst_nodes[0] }}"), t["name"]
    # - and no unreachable-tolerance is sprinkled around instead.
    for path in (PB, SRC_TASKS):
        assert "ignore_unreachable" not in path.read_text(encoding="utf-8"), path.name


_FORCE = "forced — make an Outdated target UpToDate at the DRBD level"
_FORCED_STATE = "forced — target DR state before the promote"
_FORCED_ASSERT = "forced — assert the target is UpToDate, Secondary and quorate"
_FORCED_SCORE = "forced — wait for the agent's promotion score"


def test_forced_cutover_forces_an_outdated_target_at_the_drbd_level_first() -> None:
    # #1655: ocf:linbit:drbd gives an Outdated disk no promotion score, so the forced path
    # makes the target UpToDate with `primary --force`, hands it straight back to Secondary
    # (never Primary outside Pacemaker), and only then lets Pacemaker promote it.
    force = _task(_FORCE)
    assert force["when"] == "dr_force_b and dst_disk != ['UpToDate']"
    assert force["delegate_to"] == "{{ dr_dst_nodes[0] }}"
    assert force["changed_when"] is True
    body = force["ansible.builtin.shell"]
    primary = body.index("drbdadm -S primary --force {{ pdrbd_dr_res }}")
    secondary = body.index("drbdadm -S secondary {{ pdrbd_dr_res }}")
    assert primary < secondary
    assert body.count("exit 1") == 2  # each step fails loud, with the live state
    assert body.count("drbdsetup status --verbose {{ pdrbd_dr_res }} >&2") == 2
    assert "IS PRIMARY OUTSIDE PACEMAKER" in body
    order = [_index(f) for f in (_REASSERT, _FORCE, _FORCED_STATE, _FORCED_ASSERT, _FORCED_SCORE)]
    assert order == sorted(order)
    assert order[-1] < _index("promote at the target site")


def test_forced_cutover_proves_the_target_is_promotable_before_pacemaker() -> None:
    for frag in (_FORCED_STATE, _FORCED_ASSERT, _FORCED_SCORE):
        task = _task(frag)
        assert task["when"] == "dr_force_b", frag
        assert task.get("delegate_to", "{{ dr_dst_nodes[0] }}") == "{{ dr_dst_nodes[0] }}", frag
    that = _task(_FORCED_ASSERT)["ansible.builtin.assert"]["that"]
    assert "dst_forced.stdout | regex_findall(drbd_disk_re) == ['UpToDate']" in that
    assert "dst_forced.stdout | regex_findall(drbd_role_re) == ['Secondary']" in that
    assert "dst_forced.stdout | regex_findall(drbd_quorum_re) == ['yes']" in that
    assert "promotion score" in _task(_FORCED_ASSERT)["ansible.builtin.assert"]["fail_msg"]
    # `pcs --wait` settles at once when there is no score yet, so the score is waited for,
    # bounded, and its absence fails loud with the live state.
    score = _task(_FORCED_SCORE)["ansible.builtin.shell"]
    assert "--name master-drbd_dr_qm --query" in score
    assert "--node {{ dr_dst_nodes[0] }}" in score
    assert "seq 1 45" in score
    assert "crm_mon -1 -A -r >&2" in score
    assert score.rstrip().endswith("exit 1")


def test_post_promote_assert_names_the_missing_promotion_score() -> None:
    body = _task("assert the target DR resource is Primary")["ansible.builtin.shell"]
    assert "Pacemaker promoted nothing" in body
    assert "no promotion score (master-drbd_dr_qm)" in body
    assert "crm_mon -1 -A -r >&2" in body


def test_planned_switch_skips_every_forced_step() -> None:
    # The planned path is unchanged: every new task is gated on dr_force_b.
    for frag in (_FORCE, _FORCED_STATE, _FORCED_ASSERT, _FORCED_SCORE):
        assert str(_task(frag)["when"]).startswith("dr_force_b"), frag
        variables = {"dr_force_b": False, "dst_disk": ["Outdated"]}
        assert _cond(_task(frag)["when"], variables) is False, frag
    variables = {"dr_force_b": True, "dst_disk": ["UpToDate"]}
    assert _cond(_task(_FORCE)["when"], variables) is False  # nothing to force
    variables = {"dr_force_b": True, "dst_disk": ["Outdated"]}
    assert _cond(_task(_FORCE)["when"], variables) is True


def test_dr_switch_rpo0_seed_before_demote_and_verify_after_promote() -> None:
    seed, demote = _index("RPO-0 seed"), _index("stop and demote at the source site")
    promote, verify = _index("promote at the target site"), _index("RPO-0 verify")
    assert seed < demote < promote < verify
    # The seed's path is relative to the source task file, which sits beside it in tasks/.
    assert _task("RPO-0 seed")["ansible.builtin.include_tasks"] == "rpo0-drill-seed.yml"
    assert (SRC_TASKS.parent / "rpo0-drill-seed.yml") == SEED
    assert _task("RPO-0 verify")["ansible.builtin.include_tasks"] == "tasks/rpo0-drill-verify.yml"
    for frag in ("RPO-0 seed", "RPO-0 verify"):
        assert _task(frag)["when"] == "rpo0_drill | default(false) | bool", frag
    assert "rpo0-drill-seed" in str(_source_tasks())  # the seed contacts the source


def test_dr_switch_never_masks_a_failure() -> None:
    for path in (PB, SRC_TASKS, SEED, VERIFY, SITE):
        text = path.read_text(encoding="utf-8")
        assert "|| true" not in text, path.name
        assert "failed_when: false" not in text, path.name
        assert "ignore_errors" not in text, path.name


def test_dr_switch_flips_only_the_dr_clone() -> None:
    """RDQM's switch leaves each site's HA clone running (rdqm-b1/pcmk-status.txt: HA
    Promoted on b1 under the DR secondary); only the DR clone's target-role moves."""
    demote = _task("stop and demote at the source site")["ansible.builtin.shell"]
    promote = _task("promote at the target site")["ansible.builtin.shell"]
    assert "drbd_dr_qm-clone target-role=Unpromoted" in demote
    assert "drbd_dr_qm-clone target-role=Promoted" in promote
    for path in (PB, SRC_TASKS):
        text = path.read_text(encoding="utf-8")
        assert "drbd_qm-clone" not in text.replace("drbd_dr_qm-clone", ""), path.name
    # ... and the dr-role marker with it (RDQM's rdqm:<qm>-dr-role, 1 primary / 2 secondary).
    assert "-dr-role --update 2" in demote
    assert "-dr-role --update 1" in promote


def test_dr_switch_demotes_source_before_promoting_target() -> None:
    """rdqmdr -p "fails if the primary instance of the queue manager is still running and
    the DR replication link is still functioning" (IBM, rdqmdr, 10.0.x)."""
    secondary_at = _index("assert the source DR resource is Secondary")
    assert secondary_at < _index("promote at the target site")
    secondary = _task("assert the source DR resource is Secondary")
    assert "['Secondary']" in secondary["until"]  # waited for, bounded, then loud
    assert (secondary["retries"], secondary["delay"]) == (30, 2)
    assert "failed_when" not in secondary


def test_pcs_waits_fail_loud_with_the_live_state() -> None:
    for frag in ("stop and demote at the source site", "promote at the target site"):
        body = _task(frag)["ansible.builtin.shell"]
        assert "--wait=" in body, frag
        assert "crm_mon -1 -r >&2" in body, frag
        assert "exit 1" in body, frag


def test_rpo0_tasks_mirror_the_rdqm_drill() -> None:
    seed = _load(SEED)
    assert "rpo0_token" in seed[0]["ansible.builtin.set_fact"]
    body = seed[1]["ansible.builtin.shell"]
    assert "DEFINE QLOCAL(DR.RPO0.DRILL) DEFPSIST(YES) REPLACE" in body
    assert "amqsput DR.RPO0.DRILL" in body
    verify = _load(VERIFY)
    assert verify[0]["ansible.builtin.command"]["argv"][:2] == [
        "/opt/mqm/samp/bin/amqsget",
        "DR.RPO0.DRILL",
    ]
    assert "rpo0_token in rpo0_got.stdout" in verify[1]["ansible.builtin.assert"]["that"]
    for task in (seed[1], verify[0]):
        assert task["delegate_to"] == "{{ rpo0_node }}"
        assert task["become_user"] == "mqm"


# --- the crm_mon clone-role parse (vars/pdrbd-crm-mon.yml) -----------------------------------

# This arm's HA clone, verbatim from the live #1615 run, followed by the DR clone in the
# same Pacemaker 3.0.1 layout RDQM's site A shows (rdqm-a1/pcmk-status.txt).
_PDRBD_A = (
    "Full List of Resources:\n"
    "  * Clone Set: drbd_qm-clone [drbd_qm] (promotable):\n"
    "    * Promoted: [ pdrbd-a1 ]\n"
    "    * Unpromoted: [ pdrbd-a2 pdrbd-a3 ]\n"
    "  * Clone Set: drbd_dr_qm-clone [drbd_dr_qm] (promotable):\n"
    "    * Promoted: [ pdrbd-a1 ]\n"
    "  * Resource Group: mq_group:\n"
    "    * mq_fs\t(ocf:heartbeat:Filesystem):\t Started pdrbd-a1\n"
    "    * mq_vip\t(ocf:heartbeat:IPaddr2):\t Started pdrbd-a1\n"
    "    * mq_qm\t(systemd:mq-PDRBDAPP):\t Started pdrbd-a1\n"
)


def _role_nodes(text: str, clone: str, role: str) -> list[str]:
    pattern = _load(CRM_VARS)["pdrbd_crm_role_re"].replace("CLONE", clone).replace("ROLE", role)
    return re.findall(pattern, text)


def test_crm_role_parse_on_this_arms_format() -> None:
    assert _role_nodes(_PDRBD_A, "drbd_qm-clone", "Promoted") == ["pdrbd-a1"]
    assert _role_nodes(_PDRBD_A, "drbd_qm-clone", "Unpromoted") == ["pdrbd-a2 pdrbd-a3"]
    assert _role_nodes(_PDRBD_A, "drbd_dr_qm-clone", "Promoted") == ["pdrbd-a1"]
    # Never another clone's role line: the DR clone has no Unpromoted line here.
    assert _role_nodes(_PDRBD_A, "drbd_dr_qm-clone", "Unpromoted") == []


def test_crm_role_parse_on_rdqms_dr_secondary() -> None:
    # The blueprint's site B, verbatim: the DR clone "Unpromoted (target-role)".
    text = (BLUEPRINT / "rdqm-b1" / "pcmk-status.txt").read_text(encoding="utf-8")
    assert _role_nodes(text, "ms_drbd_dr_rdqmapp", "Unpromoted") == ["rdqm-b1"]
    assert _role_nodes(text, "ms_drbd_dr_rdqmapp", "Promoted") == []
    assert _role_nodes(text, "ms_drbd_rdqmapp", "Promoted") == ["rdqm-b1"]
    assert _role_nodes(text, "ms_drbd_rdqmapp", "Unpromoted") == ["rdqm-b2 rdqm-b3"]


def test_crm_role_parse_on_rdqms_dr_primary() -> None:
    text = (BLUEPRINT / "rdqm-a1" / "pcmk-status.txt").read_text(encoding="utf-8")
    assert _role_nodes(text, "ms_drbd_dr_rdqmapp", "Promoted") == ["rdqm-a1"]
    assert _role_nodes(text, "ms_drbd_dr_rdqmapp", "Unpromoted") == []


def test_switch_and_role_share_the_one_parse() -> None:
    [play] = [p for p in _load(PB) if p["name"] == "switch"]
    assert play["vars_files"] == ["vars/pdrbd-crm-mon.yml"]
    v = _switch_vars()
    promoted = "replace('CLONE', 'drbd_dr_qm-clone') | replace('ROLE', 'Promoted')"
    assert promoted in v["dr_promoted_re"]
    assert "replace('ROLE', 'Unpromoted')" in v["dr_unpromoted_re"]
    for frag in (
        "assert the source site's DR clone is Promoted",
        "assert the target site's DR clone",
    ):
        that = _task(frag)["ansible.builtin.assert"]["that"]
        assert any("length == 1" in str(c) for c in that), frag


# --- the drbdsetup status --verbose parse ---------------------------------------------------

# Verbatim from the live #1615 run (the HA resource; the stacked one prints the same shape).
_DRBDSETUP = (
    "pdrbdapp node-id:0 role:Primary suspended:no force-io-failures:no\n"
    "  volume:0 minor:100 disk:UpToDate backing_dev:/dev/drbdpool/pdrbdapp_00 quorum:yes\n"
    "  pdrbd-a2 node-id:1 connection:Connected role:Secondary tls:no\n"
    "    volume:0 replication:Established peer-disk:UpToDate\n"
    "  pdrbd-a3 node-id:2 connection:Connected role:Secondary tls:no\n"
    "    volume:0 replication:Established peer-disk:UpToDate\n"
)


def test_drbdsetup_parse() -> None:
    v = _switch_vars()
    assert re.findall(v["drbd_role_re"], _DRBDSETUP) == ["Primary"]
    assert re.findall(v["drbd_disk_re"], _DRBDSETUP) == ["UpToDate"]
    assert re.findall(v["drbd_conn_re"], _DRBDSETUP) == ["Connected", "Connected"]
    assert re.findall(v["drbd_peer_disk_re"], _DRBDSETUP) == ["UpToDate", "UpToDate"]
    lagging = _DRBDSETUP.replace("peer-disk:UpToDate", "peer-disk:Inconsistent", 1)
    assert re.findall(v["drbd_peer_disk_re"], lagging) == ["Inconsistent", "UpToDate"]
    assert re.findall(v["drbd_quorum_re"], _DRBDSETUP) == ["yes"]


# The stacked DR resource on pdrbd-b1, verbatim from the #1430 drill 6 hold (2026-10-19):
# the link broken by a graceful disconnect, site A destroyed.
_DRBDSETUP_DR_OUTDATED = (
    "pdrbdapp.dr node-id:1 role:Secondary suspended:no force-io-failures:no\n"
    "  volume:0 minor:7001 disk:Outdated backing_dev:/dev/drbd100 quorum:yes open:no\n"
    "      blocked:no\n"
    "  encrypted.remote node-id:0 connection:Connecting role:Unknown tls:yes\n"
    "      congested:no ap-in-flight:0 rs-in-flight:0\n"
    "    volume:0 replication:Off peer-disk:DUnknown resync-suspended:no\n"
)


def test_drbdsetup_parse_on_the_held_drill6_state() -> None:
    v = _switch_vars()
    assert re.findall(v["drbd_role_re"], _DRBDSETUP_DR_OUTDATED) == ["Secondary"]
    assert re.findall(v["drbd_disk_re"], _DRBDSETUP_DR_OUTDATED) == ["Outdated"]
    assert re.findall(v["drbd_quorum_re"], _DRBDSETUP_DR_OUTDATED) == ["yes"]
    assert re.findall(v["drbd_conn_re"], _DRBDSETUP_DR_OUTDATED) == ["Connecting"]
    assert re.findall(v["drbd_peer_disk_re"], _DRBDSETUP_DR_OUTDATED) == ["DUnknown"]
    no_quorum = _DRBDSETUP_DR_OUTDATED.replace("quorum:yes", "quorum:no")
    assert re.findall(v["drbd_quorum_re"], no_quorum) == ["no"]


# --- the watched DR-sync wait (spec §7) -----------------------------------------------------


def test_provision_waits_for_dr_sync_with_a_stall_window() -> None:
    plays = [p for p in _load(SITE) if isinstance(p, dict)]
    names = [p.get("name", "") for p in plays]
    site_b = next(i for i, n in enumerate(names) if "site B" in n and "volume" in n)
    wait = next(i for i, n in enumerate(names) if "wait for DR sync" in n)
    assert site_b < wait
    body = str(plays[wait])
    assert "pdrbd_sync_stall_secs" in body
    assert "pdrbd_sync_max_secs" in body
    assert "drbdsetup status" in body  # drbdadm status all omits the stacked resource
    assert "dr_enabled" in str(plays[wait]["tasks"][0].get("when", ""))
    # in-sync needs the peer connected AND UpToDate: a missing peer is a stall, not a pass.
    shell = plays[wait]["tasks"][0]["ansible.builtin.shell"]
    assert "connection:Connected" in shell
    assert "peer-disk:UpToDate" in shell


# --- the DR-sync result is printed, and the gate reads the last line (#1635) ----------------

_PROGRESS = ["11:45:23 sync done:42.10", "11:45:33 sync done:97.80", "in-sync after 20s"]


def _cond(expr: str, variables: dict[str, Any]) -> Any:
    """A bare Ansible conditional (when/that), evaluated through the real templar."""
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template("{{ " + expr.strip() + " }}"))


def _sync_tasks() -> list[dict[str, Any]]:
    [play] = [p for p in _load(SITE) if "wait for DR sync" in p.get("name", "")]
    return list(play["tasks"])


def _sync_task(prefix: str) -> dict[str, Any]:
    (task,) = [t for t in _sync_tasks() if t["name"].startswith(prefix)]
    return task


def _registered(lines: list[str]) -> dict[str, Any]:
    return {"stdout": "\n".join(lines), "stdout_lines": lines}


def test_dr_sync_result_is_printed_between_the_poll_and_the_gate() -> None:
    names = [t["name"] for t in _sync_tasks()]
    assert names[0].startswith("poll drbdsetup status")
    assert names[1].startswith("DR sync result")
    assert names[2].startswith("exactly one site-A node")
    poll, show = _sync_tasks()[0], _sync_tasks()[1]
    assert poll["register"] == "pdrbd_dr_sync"
    # Every line, so the progress shows as well as the elapsed time.
    assert show["ansible.builtin.debug"] == {"msg": "{{ pdrbd_dr_sync.stdout_lines }}"}


@pytest.mark.parametrize(
    ("dr_enabled", "result", "shown"),
    [
        (True, _registered(_PROGRESS), True),  # caught up: progress lines, then in-sync
        (True, _registered(["in-sync after 0s"]), True),  # in sync at once
        (True, _registered(["not-here"]), False),  # does not hold the DR resource
        (False, {"skipped": True}, False),  # --no-dr: the poll was skipped, nothing to show
    ],
)
def test_dr_sync_result_shows_only_on_the_node_that_held_the_dr_resource(
    *, dr_enabled: bool, result: dict[str, Any], shown: bool
) -> None:
    when = _sync_task("DR sync result")["when"]
    assert _cond(when, {"dr_enabled": dr_enabled, "pdrbd_dr_sync": result}) is shown


@pytest.mark.parametrize(
    ("outputs", "passes"),
    [
        ([_PROGRESS, ["not-here"], ["not-here"]], True),  # progress first must still pass
        ([["not-here"], ["in-sync after 0s"], ["not-here"]], True),
        ([["not-here"], ["not-here"], ["not-here"]], False),  # nobody held it
        ([["in-sync after 0s"], ["in-sync after 3s"], ["not-here"]], False),  # two holders
    ],
)
def test_dr_sync_gate_reads_the_verdict_from_the_last_line(
    *, outputs: list[list[str]], passes: bool
) -> None:
    gate = _sync_task("exactly one site-A node")
    nodes = ["pdrbd-a1", "pdrbd-a2", "pdrbd-a3"]
    hostvars = {n: {"pdrbd_dr_sync": _registered(o)} for n, o in zip(nodes, outputs, strict=True)}
    variables = {"groups": {"pdrbd_a": nodes}, "hostvars": hostvars}
    assert _cond(gate["ansible.builtin.assert"]["that"], variables) is passes
    assert gate["run_once"] is True
