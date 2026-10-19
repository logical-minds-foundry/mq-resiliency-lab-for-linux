"""site-pdrbd-dr-rejoin.yml — a returning DR site rejoins as the DR secondary (#1660).

IBM's floor (MQ 10.0.0, "RDQM disaster recovery"): the repaired node "will be initially
made the secondary and be synchronized"; divergent data is "partitioned" and the
operator decides which set to keep ("Resolving a partitioned (split brain) problem in DR
RDQM"). Pinned statically, then run for real on localhost against stubs that keep each
node's state (crm_mon, drbdsetup) and record every call, with site A returning and site B
the live DR primary.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
PB = ANSIBLE / "site-pdrbd-dr-rejoin.yml"
SWITCH = ANSIBLE / "site-pdrbd-dr-switch.yml"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _play(path: Path, name: str) -> dict[str, Any]:
    [play] = [p for p in _load(path) if p["name"] == name]
    return play


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for play in _load(PB):
        tasks.extend(play["tasks"])
    return tasks


def _index(fragment: str) -> int:
    return next(i for i, t in enumerate(_tasks()) if fragment in t["name"])


def _task(fragment: str) -> dict[str, Any]:
    return _tasks()[_index(fragment)]


# --- static -----------------------------------------------------------------------------------


def test_rejoin_runs_on_localhost_and_validates_its_inputs_first() -> None:
    for play in _load(PB):
        assert play["hosts"] == "localhost", play["name"]
        assert play["gather_facts"] is False, play["name"]
    assert _index("assert rejoin_site and qm_name") == 0
    that = str(_task("assert rejoin_site and qm_name")["ansible.builtin.assert"]["that"])
    assert "['a', 'b']" in that


def test_every_check_precedes_every_change() -> None:
    first_change = next(i for i, t in enumerate(_tasks()) if t.get("changed_when") is True)
    assert _tasks()[first_change]["name"].startswith("returning site — DR clone to Unpromoted")
    for frag in (
        "assert the keeper runs the DR Primary and the QM",
        "assert the returning site's DR clone runs on exactly one node",
        "refuse a dual-active partition",
    ):
        assert _index(frag) < first_change, frag
        assert "Nothing was changed" in _task(frag)["ansible.builtin.assert"]["fail_msg"], frag


def test_a_dual_active_partition_needs_the_explicit_discard() -> None:
    gate = _task("refuse a dual-active partition")["ansible.builtin.assert"]
    assert gate["that"] == "back_promoted | length == 0 or dr_discard_b"
    assert "-e dr_discard=true" in gate["fail_msg"]
    assert "DISCARDS" in gate["fail_msg"]
    assert _play(PB, "rejoin")["vars"]["dr_discard_b"] == "{{ dr_discard | default(false) | bool }}"


def test_initially_made_the_secondary_is_the_switchs_demote() -> None:
    # rdqmdr -s (DR-2): the DR clone's target-role and the dr-role marker, at the
    # returning site; the HA clone is never touched.
    body = _task("returning site — DR clone to Unpromoted")["ansible.builtin.shell"]
    assert "pcs resource meta drbd_dr_qm-clone target-role=Unpromoted --wait=300" in body
    assert "--name {{ pdrbd_res }}-dr-role --update 2" in body
    assert "crm_mon -1 -r >&2" in body
    demote = _task("returning site — DR clone to Unpromoted")
    assert demote["delegate_to"] == "{{ groups[back][0] }}"
    assert "drbd_qm-clone" not in PB.read_text(encoding="utf-8").replace("drbd_dr_qm-clone", "")


def test_divergent_data_is_discarded_only_on_request() -> None:
    gate = _task("refuse to discard divergent data unless asked to")["ansible.builtin.assert"]
    assert gate["that"] == "not (dr_split | bool) or dr_discard_b"
    assert "SPLIT BRAIN" in gate["fail_msg"]
    assert _index("refuse to discard") < _index("reconnect, discarding its divergent data")
    discard = _task("reconnect, discarding its divergent data")
    assert discard["when"] == "dr_split | bool"
    assert discard["delegate_to"] == "{{ back_node }}"
    want = "drbdadm -S connect --discard-my-data {{ pdrbd_dr_res }}"
    assert want in discard["ansible.builtin.shell"]
    keeper = _task("keeper — reconnect a StandAlone DR member")
    assert keeper["when"] == "dr_split | bool"
    assert keeper["delegate_to"] == "{{ keep_nodes[0] }}"
    assert "connection:StandAlone" in keeper["ansible.builtin.shell"]
    assert "--discard-my-data" not in keeper["ansible.builtin.shell"]  # the keeper's data stays


def test_a_split_is_standalone_or_a_standalone_keeper() -> None:
    expr = _task("classify the handshake")["ansible.builtin.set_fact"]["dr_split"]
    assert "'standalone'" in expr
    assert "'connecting'" in expr
    assert "['StandAlone']" in expr


def test_parses_match_the_switch() -> None:
    mine, theirs = _play(PB, "rejoin")["vars"], _play(SWITCH, "switch")["vars"]
    keys = ("drbd_role_re", "drbd_disk_re", "drbd_conn_re", "dr_promoted_re", "dr_unpromoted_re")
    for key in keys:
        assert mine[key] == theirs[key], key


def test_rejoin_never_masks_a_failure() -> None:
    text = PB.read_text(encoding="utf-8")
    for banned in ("|| true", "failed_when: false", "ignore_errors", "ignore_unreachable"):
        assert banned not in text, banned


# --- run --------------------------------------------------------------------------------------

_CRM_KEEP = """\
Full List of Resources:
  * Clone Set: drbd_qm-clone [drbd_qm] (promotable):
    * Promoted: [ pdrbd-b1 ]
    * Unpromoted: [ pdrbd-b2 pdrbd-b3 ]
  * Clone Set: drbd_dr_qm-clone [drbd_dr_qm] (promotable):
    * Promoted: [ pdrbd-b1 ]
  * Resource Group: mq_group:
    * mq_fs\t(ocf:heartbeat:Filesystem):\t Started pdrbd-b1
    * mq_vip\t(ocf:heartbeat:IPaddr2):\t Started pdrbd-b1
    * mq_qm\t(systemd:mq-PDRBDAPP):\t Started pdrbd-b1
"""

# The returning site A after #1660: target-role still Promoted, but not promoted.
_CRM_BACK = """\
Full List of Resources:
  * Clone Set: drbd_qm-clone [drbd_qm] (promotable):
    * Promoted: [ pdrbd-a1 ]
    * Unpromoted: [ pdrbd-a2 pdrbd-a3 ]
  * Clone Set: drbd_dr_qm-clone [drbd_dr_qm] (promotable):
    * Unpromoted: [ pdrbd-a1 ]
  * Resource Group: mq_group:
    * mq_fs\t(ocf:heartbeat:Filesystem):\t Stopped
    * mq_vip\t(ocf:heartbeat:IPaddr2):\t Stopped
    * mq_qm\t(systemd:mq-PDRBDAPP):\t Stopped
"""

_CRM_BACK_AFTER = _CRM_BACK.replace(
    "* Unpromoted: [ pdrbd-a1 ]", "* Unpromoted (target-role): [ pdrbd-a1 ]"
)

# Pre-#1660 (drill 6 as found): site A promoted too.
_CRM_BACK_DUAL = _CRM_BACK.replace(
    "    * Unpromoted: [ pdrbd-a1 ]\n", "    * Promoted: [ pdrbd-a1 ]\n"
).replace("Stopped", "Started pdrbd-a1")


def _dr(role: str, conn: str, peer_role: str, peer_disk: str, repl: str = "Off") -> str:
    node_id = 0 if role == "Secondary" else 1
    return (
        f"pdrbdapp.dr node-id:{node_id} role:{role} suspended:no force-io-failures:no\n"
        "  volume:0 minor:7001 disk:UpToDate backing_dev:/dev/drbd100 quorum:yes open:no\n"
        "      blocked:no\n"
        f"  encrypted.remote node-id:{1 - node_id} connection:{conn} role:{peer_role} tls:yes\n"
        "      congested:no ap-in-flight:0 rs-in-flight:0\n"
        f"    volume:0 replication:{repl} peer-disk:{peer_disk} resync-suspended:no\n"
    )


_DR_BACK_SPLIT = _dr("Secondary", "StandAlone", "Unknown", "DUnknown")
_DR_BACK_CONNECTING = _dr("Secondary", "Connecting", "Unknown", "Outdated")
_DR_BACK_SYNCED = _dr("Secondary", "Connected", "Primary", "UpToDate", "Established")
_DR_KEEP_SPLIT = _dr("Primary", "StandAlone", "Unknown", "DUnknown")
_DR_KEEP_CONNECTED = _dr("Primary", "Connected", "Secondary", "UpToDate", "Established")

_CRM_KEEP_NOT_PRIMARY = _CRM_KEEP.replace(
    "Promoted: [ pdrbd-b1 ]\n  * Resource", "Unpromoted: [ pdrbd-b1 ]\n  * Resource"
)

_NODES = ("pdrbd-a1", "pdrbd-a2", "pdrbd-a3", "pdrbd-b1", "pdrbd-b2", "pdrbd-b3")


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    tmp: Path,
    *extra: str,
    crm_back: str = _CRM_BACK,
    crm_keep: str = _CRM_KEEP,
    dr_back: str = _DR_BACK_SPLIT,
    dr_keep: str = _DR_KEEP_SPLIT,
) -> subprocess.CompletedProcess[str]:
    """Every node is localhost, told apart by STUB_HOST, which a per-node interpreter
    wrapper exports (delegate_to uses the delegated node's ansible_python_interpreter)."""
    bindir = tmp / "bin"
    bindir.mkdir()
    st = tmp / "state"
    st.mkdir()
    log = tmp / "calls.log"
    (st / "crm_mon.pdrbd-a1").write_text(crm_back, encoding="utf-8")
    (st / "crm_mon.pdrbd-b1").write_text(crm_keep, encoding="utf-8")
    (st / "crm_mon.after").write_text(_CRM_BACK_AFTER, encoding="utf-8")
    (st / "drbd.pdrbd-a1").write_text(dr_back, encoding="utf-8")
    (st / "drbd.pdrbd-b1").write_text(dr_keep, encoding="utf-8")
    (st / "drbd.back_synced").write_text(_DR_BACK_SYNCED, encoding="utf-8")
    (st / "drbd.keep_connected").write_text(_DR_KEEP_CONNECTED, encoding="utf-8")
    record = f'echo "$STUB_HOST $(basename "$0") $*" >> {log}\n'
    _stub(bindir, "crm_mon", record + f"cat {st}/crm_mon.$STUB_HOST\n")
    _stub(bindir, "drbdsetup", record + f"cat {st}/drbd.$STUB_HOST\n")
    _stub(
        bindir,
        "pcs",
        record + 'case "$*" in *target-role=Unpromoted*) '
        f"cp {st}/crm_mon.after {st}/crm_mon.$STUB_HOST ;; esac\n",
    )
    _stub(
        bindir,
        "drbdadm",
        record + 'case "$*" in\n'
        f"  *--discard-my-data*) cp {st}/drbd.back_synced {st}/drbd.$STUB_HOST ;;\n"
        f'  *" connect "*) cp {st}/drbd.keep_connected {st}/drbd.$STUB_HOST ;;\n'
        "esac\n",
    )
    _stub(bindir, "crm_attribute", record)
    hosts = []
    for node in _NODES:
        wrapper = bindir / f"py-{node}"
        wrapper.write_text(
            f'#!/bin/sh\nexport STUB_HOST={node}\nexec {sys.executable} "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
        hosts.append(f"{node} ansible_connection=local ansible_python_interpreter={wrapper}")
    inventory = tmp / "inventory.ini"
    inventory.write_text(
        "[pdrbd_a]\n" + "\n".join(hosts[:3]) + "\n\n[pdrbd_b]\n" + "\n".join(hosts[3:]) + "\n\n"
        "[local]\nlocalhost ansible_connection=local "
        f"ansible_python_interpreter={sys.executable}\n\n"
        "[all:vars]\nansible_become=false\n",
        encoding="utf-8",
    )
    cfg = tmp / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled = False\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "ANSIBLE_CONFIG": str(cfg),
        "ANSIBLE_HOME": str(tmp / "ansible-home"),
        "ANSIBLE_LOCAL_TEMP": str(tmp / "local-tmp"),
        "ANSIBLE_REMOTE_TMP": str(tmp / "remote-tmp"),
        "ANSIBLE_NOCOLOR": "1",
    }
    argv = [sys.executable, "-m", "ansible.cli.playbook", "-i", str(inventory), str(PB)]
    argv += ["-e", "rejoin_site=a", "-e", "qm_name=PDRBDAPP", *extra]
    return subprocess.run(  # noqa: S603 - fixed argv, test-owned inputs
        argv, cwd=PB.parent, env=env, capture_output=True, text=True, check=False, timeout=300
    )


_DEMOTE = "pdrbd-a1 pcs resource meta drbd_dr_qm-clone target-role=Unpromoted --wait=300"


def _calls(tmp: Path) -> list[str]:
    return (tmp / "calls.log").read_text(encoding="utf-8").splitlines()


def test_a_split_brain_stops_after_the_demote_of_record(tmp_path: Path) -> None:
    run = _run(tmp_path)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert "SPLIT BRAIN" in out
    calls = _calls(tmp_path)
    assert _DEMOTE in calls
    assert not [c for c in calls if " drbdadm " in c]  # no data was touched
    assert not [c for c in calls if c.startswith("pdrbd-b1 pcs")]  # the keeper was not changed


def test_a_split_brain_with_discard_resyncs_the_returning_site(tmp_path: Path) -> None:
    run = _run(tmp_path, "-e", "dr_discard=true")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    calls = _calls(tmp_path)
    order = [
        calls.index(_DEMOTE),
        calls.index("pdrbd-a1 drbdadm -S connect --discard-my-data pdrbdapp.dr"),
        calls.index("pdrbd-b1 drbdadm -S connect pdrbdapp.dr"),
    ]
    assert order == sorted(order), calls
    assert "pdrbd-a1 drbdadm -S disconnect pdrbdapp.dr" not in calls  # already StandAlone
    assert "mqlab dr failback pcmk-drbd-ubuntu --rpo0-drill" in out


def test_a_connecting_member_facing_a_standalone_keeper_is_a_split(tmp_path: Path) -> None:
    # The returning member's DR resource restarted after the split: it is Connecting,
    # and the StandAlone keeper never answers. Disconnect, then connect discarding.
    run = _run(
        tmp_path,
        "-e",
        "dr_discard=true",
        "-e",
        "pdrbd_handshake_secs=2",
        dr_back=_DR_BACK_CONNECTING,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    calls = _calls(tmp_path)
    assert calls.index("pdrbd-a1 drbdadm -S disconnect pdrbdapp.dr") < calls.index(
        "pdrbd-a1 drbdadm -S connect --discard-my-data pdrbdapp.dr"
    )


def test_no_divergence_needs_no_discard(tmp_path: Path) -> None:
    # DRBD resynced the returning member by itself: only the demote of record.
    run = _run(tmp_path, dr_back=_DR_BACK_SYNCED, dr_keep=_DR_KEEP_CONNECTED)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not [c for c in _calls(tmp_path) if " drbdadm " in c]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"crm_keep": _CRM_KEEP_NOT_PRIMARY},
         "is not the live DR primary"),
        ({"crm_back": _CRM_BACK_DUAL}, "BOTH sites run the DR Primary"),
        ({"crm_back": _CRM_BACK.replace("    * Unpromoted: [ pdrbd-a1 ]\n", "")},
         "is not running on exactly one node"),
    ],
)  # fmt: skip
def test_preflight_refusals_change_nothing(
    tmp_path: Path, kwargs: dict[str, str], message: str
) -> None:
    run = _run(tmp_path, **kwargs)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert message in out
    assert not [c for c in _calls(tmp_path) if " pcs " in c or " drbdadm " in c]


def test_a_dual_active_partition_with_discard_demotes_the_returning_site(tmp_path: Path) -> None:
    run = _run(tmp_path, "-e", "dr_discard=true", crm_back=_CRM_BACK_DUAL)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "pdrbd-a1 drbdadm -S connect --discard-my-data pdrbdapp.dr" in _calls(tmp_path)


def test_a_dr_link_that_stays_down_fails_loud_without_touching_data(tmp_path: Path) -> None:
    keep_connecting = _DR_KEEP_SPLIT.replace("connection:StandAlone", "connection:Connecting")
    run = _run(
        tmp_path,
        "-e",
        "dr_discard=true",
        "-e",
        "pdrbd_handshake_secs=2",
        dr_back=_DR_BACK_CONNECTING,
        dr_keep=keep_connecting,
    )
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert "cannot reach" in out
    assert not [c for c in _calls(tmp_path) if " drbdadm " in c]
