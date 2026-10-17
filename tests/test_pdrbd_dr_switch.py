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

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
PB = ANSIBLE / "site-pdrbd-dr-switch.yml"
SITE = ANSIBLE / "site-pdrbd.yml"
SEED = ANSIBLE / "tasks" / "rpo0-drill-seed.yml"
VERIFY = ANSIBLE / "tasks" / "rpo0-drill-verify.yml"
CRM_VARS = ANSIBLE / "vars" / "pdrbd-crm-mon.yml"
BLUEPRINT = REPO_ROOT / "docs" / "reference" / "rdqm-blueprint"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_tasks() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for play in _load(PB):
        for t in play.get("tasks", []):
            out.extend((play["name"], inner) for inner in t.get("block", [t]))
    return out


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


def test_dr_switch_asserts_disk_state_before_promote() -> None:
    assert _index("assert the target's disk is UpToDate") < _index("promote at the target site")


def test_dr_switch_force_is_explicit() -> None:
    gate = _task("assert the target's disk is UpToDate")
    that = str(gate["ansible.builtin.assert"]["that"])
    assert "dr_force" in that
    # A planned switch needs the link up and the source's disk UpToDate too: a
    # disconnected target can call itself UpToDate while missing protocol-A writes.
    assert "dst_conn == ['Connected']" in that
    assert "dst_peer_disk == ['UpToDate']" in that
    assert "refusing to promote" in gate["ansible.builtin.assert"]["fail_msg"]
    assert "dr_force_b" in _switch_vars()
    assert _switch_vars()["dr_force_b"] == "{{ dr_force | default(false) | bool }}"


def test_forced_cutover_refuses_the_rpo0_drill() -> None:
    that = str(_task("assert dr_direction")["ansible.builtin.assert"]["that"])
    assert "dr_force" in that
    assert "rpo0_drill" in that


def test_source_steps_are_skipped_only_on_a_forced_cutover() -> None:
    for frag in (
        "source site crm_mon",
        "find the source site's DR-promoted node",
        "assert the source site's DR clone is Promoted",
        "stop and demote at the source site",
        "assert the source DR resource is Secondary",
    ):
        assert _task(frag)["when"] == "not dr_force_b", frag
    for frag in ("target site crm_mon", "promote at the target site"):
        assert "when" not in _task(frag), frag


def test_dr_switch_rpo0_seed_before_demote_and_verify_after_promote() -> None:
    seed, demote = _index("RPO-0 seed"), _index("stop and demote at the source site")
    promote, verify = _index("promote at the target site"), _index("RPO-0 verify")
    assert seed < demote < promote < verify
    assert _task("RPO-0 seed")["ansible.builtin.include_tasks"] == "tasks/rpo0-drill-seed.yml"
    assert _task("RPO-0 verify")["ansible.builtin.include_tasks"] == "tasks/rpo0-drill-verify.yml"
    for frag in ("RPO-0 seed", "RPO-0 verify"):
        assert _task(frag)["when"] == "rpo0_drill | default(false) | bool", frag


def test_dr_switch_never_masks_a_failure() -> None:
    for path in (PB, SEED, VERIFY, SITE):
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
    assert "drbd_qm-clone" not in PB.read_text(encoding="utf-8").replace("drbd_dr_qm-clone", "")
    # ... and the dr-role marker with it (RDQM's rdqm:<qm>-dr-role, 1 primary / 2 secondary).
    assert "-dr-role --update 2" in demote
    assert "-dr-role --update 1" in promote


def test_dr_switch_demotes_source_before_promoting_target() -> None:
    """rdqmdr -p "fails if the primary instance of the queue manager is still running and
    the DR replication link is still functioning" (IBM, rdqmdr, 10.0.x)."""
    secondary_at = _index("assert the source DR resource is Secondary")
    assert secondary_at < _index("promote at the target site")
    secondary = _task("assert the source DR resource is Secondary")
    assert "['Secondary']" in secondary["failed_when"]


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
