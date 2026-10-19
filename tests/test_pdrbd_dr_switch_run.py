"""site-pdrbd-dr-switch.yml, run for real by ansible-playbook on localhost (#1647).

The structure tests (test_pdrbd_dr_switch.py) pin where each task sits, but not what
ansible-core does at run time. #1430 drill 6 hit that gap: on ansible-core 2.21 the
executor templates a task's `delegate_to` BEFORE it evaluates the task's `when`
(TaskExecutor._execute_internal calls _calculate_delegate_to first). Since 2.19 an
undefined variable there is an error, where 2.18 templated it with
fail_on_undefined=False. So a source task delegated to `dr_src_nodes[0]` failed the forced
path even though its gated block was skipped.

These tests run the real playbook. The target site's commands (crm_mon, drbdsetup, pcs,
crm_attribute) are stubs on PATH, behind a local connection. Every source node is bound
to a connection plugin that does not exist, so any contact with the source fails the run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PB = REPO_ROOT / "ansible" / "site-pdrbd-dr-switch.yml"

# Site B as the DR secondary: HA and DR members on pdrbd-b1, the QM's group Started
# there (static, so it also satisfies the post-promote assert).
_CRM_MON_B = """\
Full List of Resources:
  * Clone Set: drbd_qm-clone [drbd_qm] (promotable):
    * Promoted: [ pdrbd-b1 ]
    * Unpromoted: [ pdrbd-b2 pdrbd-b3 ]
  * Clone Set: drbd_dr_qm-clone [drbd_dr_qm] (promotable):
    * Unpromoted: [ pdrbd-b1 ]
  * Resource Group: mq_group:
    * mq_fs\t(ocf:heartbeat:Filesystem):\t Started pdrbd-b1
    * mq_vip\t(ocf:heartbeat:IPaddr2):\t Started pdrbd-b1
    * mq_qm\t(systemd:mq-PDRBDAPP):\t Started pdrbd-b1
"""

# Drill 6's held state (pdrbd-b1, 2026-10-19): the DR link broken by a graceful
# disconnect and site A gone, so the target is Outdated, Secondary, still quorate.
_DRBD_LOST_SOURCE = """\
pdrbdapp.dr node-id:1 role:Secondary suspended:no force-io-failures:no
  volume:0 minor:7001 disk:Outdated backing_dev:/dev/drbd100 quorum:yes open:no
  encrypted.remote node-id:0 connection:Connecting role:Unknown tls:yes
    volume:0 replication:Off peer-disk:DUnknown resync-suspended:no
"""

# After `primary --force` then `secondary`: UpToDate, Secondary, the peer still unknown.
_DRBD_FORCED = _DRBD_LOST_SOURCE.replace("disk:Outdated", "disk:UpToDate")

# After Pacemaker promotes it.
_DRBD_PROMOTED = _DRBD_FORCED.replace("role:Secondary", "role:Primary")

# A healthy link, so a planned switch passes the pre-flight and goes on to the source.
_DRBD_IN_SYNC = """\
pdrbdapp.dr node-id:1 role:Primary suspended:no force-io-failures:no
  volume:0 minor:101 disk:UpToDate backing_dev:/dev/drbd100 quorum:yes
  pdrbd-a1 node-id:0 connection:Connected role:Primary tls:no
    volume:0 replication:Established peer-disk:UpToDate
"""

_INVENTORY = """\
[pdrbd_a]
pdrbd-a1
pdrbd-a2
pdrbd-a3

[pdrbd_a:vars]
ansible_connection=source_site_must_not_be_contacted

[pdrbd_b]
pdrbd-b1
pdrbd-b2
pdrbd-b3

[pdrbd_b:vars]
ansible_connection=local

[local]
localhost ansible_connection=local

[all:vars]
ansible_become=false
ansible_python_interpreter={python}
"""


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    drbdsetup: str,
    *extra: str,
    forced: str = _DRBD_FORCED,
    drbdadm_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run the playbook. The DRBD stubs keep state: `drbdadm secondary` leaves `forced`
    behind (the state after the DRBD-level force), and the pcs promote leaves the resource
    Primary. `drbdadm_rc` is what `drbdadm primary` exits with (11: DRBD refused it)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    state = tmp_path / "drbdsetup.out"
    (tmp_path / "crm_mon.out").write_text(_CRM_MON_B, encoding="utf-8")
    state.write_text(drbdsetup, encoding="utf-8")
    (tmp_path / "forced.out").write_text(forced, encoding="utf-8")
    (tmp_path / "promoted.out").write_text(_DRBD_PROMOTED, encoding="utf-8")
    record = f'echo "$(basename "$0") $*" >> {log}\n'
    _stub(bindir, "crm_mon", record + f"cat {tmp_path / 'crm_mon.out'}\n")
    _stub(bindir, "drbdsetup", record + f"cat {state}\n")
    drbdadm = (
        'case "$*" in\n'
        f"  *' primary '*) exit {drbdadm_rc} ;;\n"
        f"  *' secondary '*) cp {tmp_path / 'forced.out'} {state} ;;\n"
        "esac\n"
    )
    pcs = f'case "$*" in *target-role=Promoted*) cp {tmp_path / "promoted.out"} {state} ;; esac\n'
    _stub(bindir, "drbdadm", record + drbdadm)
    _stub(bindir, "pcs", record + pcs)
    _stub(bindir, "crm_attribute", record + 'case "$*" in *--query*) echo 1000 ;; esac\n')
    inventory = tmp_path / "inventory.ini"
    inventory.write_text(_INVENTORY.format(python=sys.executable), encoding="utf-8")
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\nretry_files_enabled = False\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "ANSIBLE_CONFIG": str(cfg),
        "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
        "ANSIBLE_LOCAL_TEMP": str(tmp_path / "local-tmp"),
        "ANSIBLE_REMOTE_TMP": str(tmp_path / "remote-tmp"),
        "ANSIBLE_NOCOLOR": "1",
    }
    argv = [sys.executable, "-m", "ansible.cli.playbook", "-i", str(inventory), str(PB)]
    argv += ["-e", "dr_direction=a2b", "-e", "qm_name=PDRBDAPP", *extra]
    return subprocess.run(  # noqa: S603 - fixed argv, test-owned inputs
        argv, cwd=PB.parent, env=env, capture_output=True, text=True, check=False, timeout=300
    )


def test_forced_cutover_runs_past_the_source_and_promotes_the_target(tmp_path: Path) -> None:
    # Drill 6: site A destroyed, the link broken. The forced path must never template a
    # source-only variable nor contact a source node, and must reach the promote.
    run = _run(tmp_path, _DRBD_LOST_SOURCE, "-e", "dr_force=true")
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    assert "dr_src_nodes" not in out
    assert "source_site_must_not_be_contacted" not in out
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "pcs resource meta drbd_dr_qm-clone target-role=Promoted --wait=600" in calls
    assert "target-role=Unpromoted" not in calls  # the source demote never ran
    assert "--name pdrbdapp-dr-role --update 1" in calls
    # #1655: the Outdated target is forced at the DRBD level, handed back to Secondary,
    # scored by the agent, and only then promoted by Pacemaker.
    lines = calls.splitlines()
    order = [
        lines.index("drbdadm -S primary --force pdrbdapp.dr"),
        lines.index("drbdadm -S secondary pdrbdapp.dr"),
        next(i for i, c in enumerate(lines) if "master-drbd_dr_qm --query" in c),
        next(i for i, c in enumerate(lines) if "target-role=Promoted" in c),
    ]
    assert order == sorted(order), calls


def test_forced_cutover_of_an_uptodate_target_skips_the_drbd_level_force(
    tmp_path: Path,
) -> None:
    # A target that is still UpToDate already has a score: no `primary --force`.
    run = _run(tmp_path, _DRBD_FORCED, "-e", "dr_force=true")
    assert run.returncode == 0, run.stdout + run.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "drbdadm" not in calls
    assert "master-drbd_dr_qm --query" in calls
    assert "target-role=Promoted --wait=600" in calls


def test_forced_cutover_fails_loud_when_drbd_refuses_the_force(tmp_path: Path) -> None:
    run = _run(tmp_path, _DRBD_LOST_SOURCE, "-e", "dr_force=true", drbdadm_rc=11)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert "primary --force pdrbdapp.dr was refused" in out
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "secondary" not in calls
    assert "pcs" not in calls  # Pacemaker was never asked to promote


def test_forced_cutover_refuses_a_target_without_quorum(tmp_path: Path) -> None:
    # The agent scores nothing without quorum: refuse before Pacemaker, naming it.
    no_quorum = _DRBD_FORCED.replace("quorum:yes", "quorum:no")
    run = _run(tmp_path, _DRBD_LOST_SOURCE, "-e", "dr_force=true", forced=no_quorum)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert "quorum no" in out
    assert "gives a promotion score only to an UpToDate disk with quorum" in out
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "pcs" not in calls


def test_planned_switch_still_contacts_the_source(tmp_path: Path) -> None:
    # The converse: without dr_force the source steps DO run. With the source bound to a
    # missing connection plugin, the first source task fails, after the pre-flight passed
    # and before anything was changed.
    run = _run(tmp_path, _DRBD_IN_SYNC)
    out = run.stdout + run.stderr
    assert run.returncode != 0, out
    assert "TASK [source site crm_mon]" in out
    assert "source_site_must_not_be_contacted" in out
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "pcs" not in calls  # nothing changed at either site
