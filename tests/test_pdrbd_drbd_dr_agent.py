"""ocf:pdrbd:drbd-dr — the DR clone's agent (#1660), run for real against stubs.

The wrapper runs ocf:linbit:drbd unchanged and adds one rule: a DR member that is not
Primary is promotable only while Connected to a Secondary peer with its own disk
UpToDate, or under the forced DR's one-shot grant. Here the LINBIT agent is a stub that
records each action and the adjust_master_score it was given ("0 0 0 0" means the agent
removes the promotion score), and drbdsetup / crm_attribute / crm_node / logger are
stubs on PATH. The DRBD states are live captures from pdrbd-a1 / pdrbd-b1 (2026-10-19)
and the drill 6 shapes (#1430, #1655, #1660).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = REPO_ROOT / "ansible" / "roles" / "pdrbd-qm" / "files" / "drbd-dr.sh"
GRANT = "pdrbd-dr-force-promote-pdrbdapp.dr"
ZERO = "0 0 0 0"

# Live, pdrbd-b1 (DR secondary site) with site A the DR primary: connected to a Primary.
CONNECTED_TO_PRIMARY = """\
pdrbdapp.dr node-id:1 role:Secondary suspended:no force-io-failures:no
  volume:0 minor:7001 disk:UpToDate backing_dev:/dev/drbd100 quorum:yes open:no
      blocked:no
  encrypted.remote node-id:0 connection:Connected role:Primary tls:yes congested:no
      ap-in-flight:0 rs-in-flight:0
    volume:0 replication:Established peer-disk:UpToDate resync-suspended:no
"""

# Live, pdrbd-a1 (DR primary).
PRIMARY_CONNECTED = """\
pdrbdapp.dr node-id:0 role:Primary suspended:no force-io-failures:no
  volume:0 minor:7001 disk:UpToDate backing_dev:/dev/drbd100 quorum:yes open:yes
      blocked:no
  encrypted.remote node-id:1 connection:Connected role:Secondary tls:yes
      congested:no ap-in-flight:0 rs-in-flight:0
    volume:0 replication:Established peer-disk:UpToDate resync-suspended:no
"""

# A planned switch's target once the source demoted: both Secondary, connected.
CONNECTED_TO_SECONDARY = CONNECTED_TO_PRIMARY.replace(
    "connection:Connected role:Primary", "connection:Connected role:Secondary"
)

# Drill 6's returning a1: UpToDate, Secondary, the peer remembered as Outdated (the
# graceful-disconnect flag) while it is still Connecting. ocf:linbit:drbd scores this
# 10000 — a KNOWN peer disk state.
RETURNING_PEER_OUTDATED = """\
pdrbdapp.dr node-id:0 role:Secondary suspended:no force-io-failures:no
  volume:0 minor:7001 disk:UpToDate backing_dev:/dev/drbd100 quorum:yes open:no
      blocked:no
  encrypted.remote node-id:1 connection:Connecting role:Unknown tls:yes
      congested:no ap-in-flight:0 rs-in-flight:0
    volume:0 replication:Off peer-disk:Outdated resync-suspended:no
"""

RETURNING_PEER_UNKNOWN = RETURNING_PEER_OUTDATED.replace("peer-disk:Outdated", "peer-disk:DUnknown")
SPLIT_BRAIN = RETURNING_PEER_UNKNOWN.replace("connection:Connecting", "connection:StandAlone")
PRIMARY_DISCONNECTED = PRIMARY_CONNECTED.replace(
    "connection:Connected role:Secondary", "connection:Connecting role:Unknown"
)
SYNC_TARGET = CONNECTED_TO_SECONDARY.replace("disk:UpToDate backing", "disk:Inconsistent backing")


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


class Box:
    """A node: the stubs, their call log, and the wrapper run against them."""

    def __init__(self, tmp: Path, states: list[str], *, grant: str | None = None) -> None:
        self.tmp = tmp
        self.log = tmp / "calls.log"
        bindir = tmp / "bin"
        bindir.mkdir()
        agent_dir = tmp / "ocf" / "resource.d" / "linbit"
        agent_dir.mkdir(parents=True)
        for i, st in enumerate(states):
            (tmp / f"state.{i}").write_text(st, encoding="utf-8")
        (tmp / "state.count").write_text("0", encoding="utf-8")
        if grant is not None:
            (tmp / "grant").write_text(grant, encoding="utf-8")
        log = self.log
        last = len(states) - 1
        # drbdsetup: the next state on each call, then the last one forever; no state = not up.
        _stub(
            bindir,
            "drbdsetup",
            f'echo "drbdsetup $*" >> {log}\n'
            f"n=$(cat {tmp}/state.count)\n"
            f"[ $n -gt {last} ] && n={last}\n"
            f"echo $(( n + 1 )) > {tmp}/state.count\n"
            f'[ -f {tmp}/state.$n ] || {{ echo "No such resource" >&2; exit 10; }}\n'
            f"cat {tmp}/state.$n\n",
        )
        _stub(bindir, "crm_node", f'echo "crm_node $*" >> {log}\necho pdrbd-b1\n')
        _stub(bindir, "logger", f'echo "logger $*" >> {log}\n')
        _stub(
            bindir,
            "crm_attribute",
            f'echo "crm_attribute $*" >> {log}\n'
            f'if [ -f {tmp}/crm_attribute.rc ]; then exit "$(cat {tmp}/crm_attribute.rc)"; fi\n'
            'case "$*" in\n'
            f"  *--query*) [ -f {tmp}/grant ] || exit 105; cat {tmp}/grant ;;\n"
            f"  *--delete*) rm -f {tmp}/grant ;;\n"
            "esac\n",
        )
        # The LINBIT agent: records the action and the scores it was handed.
        _stub(
            agent_dir,
            "drbd",
            f'echo "linbit $1 scores=${{OCF_RESKEY_adjust_master_score-UNSET}}" >> {log}\n'
            'if [ "$1" = meta-data ]; then\n'
            '  echo \'<resource-agent name="drbd" version="LINBIT 1.4">\'\n'
            "  echo '<parameter name=\"adjust_master_score\">'\n"
            "fi\n"
            f"rcf={tmp}/linbit.$1.rc\n"
            '[ -f "$rcf" ] && exit "$(cat "$rcf")"\n'
            "exit 0\n",
        )
        self.env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "OCF_ROOT": str(tmp / "ocf"),
            "OCF_RESKEY_drbd_resource": "pdrbdapp.dr",
            "PDRBD_DR_CONNECT_WAIT_SECS": "2",
        }

    def rc(self, action: str, rc: int) -> None:
        (self.tmp / f"linbit.{action}.rc").write_text(str(rc), encoding="utf-8")

    def run(self, action: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, test-owned stubs
            ["bash", str(AGENT), action],  # noqa: S607 - bash from PATH
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def linbit(self) -> list[str]:
        return [c.removeprefix("linbit ") for c in self.calls() if c.startswith("linbit ")]


# --- the score: what the LINBIT agent is handed on monitor / notify / reload ----------------


@pytest.mark.parametrize(
    ("state", "grant", "zeroed"),
    [
        (RETURNING_PEER_OUTDATED, None, True),  # drill 6: the LINBIT agent alone gives 10000
        (RETURNING_PEER_UNKNOWN, None, True),  # the LINBIT agent alone gives local_ok (1000)
        (SPLIT_BRAIN, None, True),  # StandAlone after a split brain
        (CONNECTED_TO_PRIMARY, None, True),  # the other site runs the DR primary
        (SYNC_TARGET, None, True),  # own disk not UpToDate
        (CONNECTED_TO_SECONDARY, None, False),  # cold boot / planned switch: promotable
        (PRIMARY_CONNECTED, None, False),  # the DR primary keeps its score
        (PRIMARY_DISCONNECTED, None, False),  # ... through a DR link outage
        (RETURNING_PEER_UNKNOWN, "1", False),  # the forced DR's grant
        (RETURNING_PEER_UNKNOWN, "0", True),  # only the value 1 is a grant
        ("", None, True),  # not up on this node (a probe)
    ],
)
@pytest.mark.parametrize("action", ["monitor", "notify", "reload"])
def test_the_score_follows_the_connection_rule(
    tmp_path: Path, action: str, state: str, grant: str | None, *, zeroed: bool
) -> None:
    box = Box(tmp_path, [state] if state else [], grant=grant)
    run = box.run(action, OCF_RESKEY_adjust_master_score="0 10 1000 10000")
    assert run.returncode == 0, run.stderr
    want = ZERO if zeroed else "0 10 1000 10000"
    assert box.linbit() == [f"{action} scores={want}"]


def test_an_unset_score_parameter_stays_the_agents_default(tmp_path: Path) -> None:
    # The DR clone sets no adjust_master_score: when promotable, the agent's own default.
    box = Box(tmp_path, [CONNECTED_TO_SECONDARY])
    assert box.run("monitor").returncode == 0
    assert box.linbit() == ["monitor scores=UNSET"]


def test_the_agents_exit_code_is_returned(tmp_path: Path) -> None:
    box = Box(tmp_path, [CONNECTED_TO_PRIMARY])
    box.rc("monitor", 7)
    assert box.run("monitor").returncode == 7


def test_a_failing_grant_query_is_no_grant_and_is_logged(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN], grant="1")
    (tmp_path / "crm_attribute.rc").write_text("1", encoding="utf-8")
    assert box.run("monitor").returncode == 0
    assert box.linbit() == [f"monitor scores={ZERO}"]
    assert any("reading the forced-promote grant as absent" in c for c in box.calls())


def test_the_grant_is_read_on_this_node_as_a_transient_attribute(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN], grant="1")
    box.run("monitor")
    want = f"crm_attribute --node pdrbd-b1 --lifetime reboot --name {GRANT} --query --quiet"
    assert want in box.calls()


# --- promote: the backstop ------------------------------------------------------------------


@pytest.mark.parametrize("state", [RETURNING_PEER_OUTDATED, SPLIT_BRAIN, CONNECTED_TO_PRIMARY])
def test_promote_is_refused_without_a_connected_secondary_peer(tmp_path: Path, state: str) -> None:
    box = Box(tmp_path, [state])
    run = box.run("promote")
    assert run.returncode == 1
    assert "ocf-exit-reason:refusing to promote pdrbdapp.dr" in run.stderr
    # the LINBIT promote never ran; its monitor ran with zero scores, removing the score
    assert box.linbit() == [f"monitor scores={ZERO}"]
    crit = "logger -t pdrbd-drbd-dr[pdrbdapp.dr] -p daemon.crit"
    assert any(c.startswith(crit) for c in box.calls())


def test_a_refused_promote_logs_a_failed_score_removal(tmp_path: Path) -> None:
    box = Box(tmp_path, [SPLIT_BRAIN])
    box.rc("monitor", 1)
    assert box.run("promote").returncode == 1
    assert any("removing the promotion score failed" in c for c in box.calls())


@pytest.mark.parametrize("state", [CONNECTED_TO_SECONDARY, PRIMARY_CONNECTED, PRIMARY_DISCONNECTED])
def test_promote_runs_the_agent_when_the_rule_allows(tmp_path: Path, state: str) -> None:
    box = Box(tmp_path, [state], grant="1")
    box.rc("promote", 0)
    assert box.run("promote").returncode == 0
    assert box.linbit() == ["promote scores=UNSET"]
    assert (tmp_path / "grant").exists()  # not the grant's promote: left to the playbook


def test_a_granted_promote_consumes_the_grant(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN], grant="1")
    assert box.run("promote").returncode == 0
    assert box.linbit() == ["promote scores=UNSET"]
    assert not (tmp_path / "grant").exists()
    assert f"crm_attribute --node pdrbd-b1 --lifetime reboot --name {GRANT} --delete" in box.calls()


def test_a_failed_granted_promote_keeps_the_grant_and_its_exit_code(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN], grant="1")
    box.rc("promote", 1)
    assert box.run("promote").returncode == 1
    assert (tmp_path / "grant").exists()


def test_a_failed_grant_delete_is_logged_not_hidden(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN], grant="1")
    # every crm_attribute call after the query fails
    stub = tmp_path / "bin" / "crm_attribute"
    stub.write_text(
        stub.read_text(encoding="utf-8").replace("rm -f", "exit 1; rm -f"), encoding="utf-8"
    )
    assert box.run("promote").returncode == 0
    assert any("deleting pdrbd-dr-force-promote-pdrbdapp.dr failed" in c for c in box.calls())


# --- start: no score until the handshake allows it ------------------------------------------


def test_start_scores_once_connected_to_a_secondary(tmp_path: Path) -> None:
    # Cold boot with the DR peer reachable: Connecting at first, then Connected.
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN, RETURNING_PEER_UNKNOWN, CONNECTED_TO_SECONDARY])
    run = box.run(
        "start", OCF_RESKEY_adjust_master_score="0 10 1000 10000", PDRBD_DR_CONNECT_WAIT_SECS="20"
    )
    assert run.returncode == 0, run.stderr
    assert box.linbit() == [f"start scores={ZERO}", "monitor scores=0 10 1000 10000"]


def test_start_restores_an_unset_score_parameter(tmp_path: Path) -> None:
    box = Box(tmp_path, [CONNECTED_TO_SECONDARY])
    assert box.run("start").returncode == 0
    assert box.linbit() == [f"start scores={ZERO}", "monitor scores=UNSET"]


@pytest.mark.parametrize("state", [SPLIT_BRAIN, CONNECTED_TO_PRIMARY])
def test_start_of_a_returning_site_gives_no_score(tmp_path: Path, state: str) -> None:
    box = Box(tmp_path, [state])
    assert box.run("start").returncode == 0
    assert box.linbit() == [f"start scores={ZERO}"]
    assert any("this site stays DR secondary" in c for c in box.calls())


def test_start_with_the_peer_unreachable_gives_up_waiting(tmp_path: Path) -> None:
    box = Box(tmp_path, [RETURNING_PEER_UNKNOWN])
    assert box.run("start").returncode == 0
    assert box.linbit() == [f"start scores={ZERO}"]
    assert any("started without a promotion score after 2s" in c for c in box.calls())


def test_a_failed_start_is_returned_without_waiting(tmp_path: Path) -> None:
    box = Box(tmp_path, [CONNECTED_TO_SECONDARY])
    box.rc("start", 1)
    assert box.run("start").returncode == 1
    assert box.linbit() == [f"start scores={ZERO}"]


def test_a_failed_post_start_monitor_is_logged(tmp_path: Path) -> None:
    box = Box(tmp_path, [CONNECTED_TO_SECONDARY])
    box.rc("monitor", 1)
    assert box.run("start").returncode == 0
    assert any("post-start monitor exited 1" in c for c in box.calls())


# --- everything else is the LINBIT agent unchanged ------------------------------------------


@pytest.mark.parametrize("action", ["stop", "demote", "validate-all", "status", "usage"])
def test_other_actions_pass_through(tmp_path: Path, action: str) -> None:
    box = Box(tmp_path, [SPLIT_BRAIN])
    box.rc(action, 0)
    assert box.run(action).returncode == 0
    assert box.linbit() == [f"{action} scores=UNSET"]
    assert not any(c.startswith("drbdsetup") for c in box.calls())


def test_meta_data_is_the_linbit_agents_renamed(tmp_path: Path) -> None:
    box = Box(tmp_path, [])
    run = box.run("meta-data", OCF_RESKEY_drbd_resource="")
    assert run.returncode == 0
    assert '<resource-agent name="drbd-dr" version="LINBIT 1.4">' in run.stdout
    assert 'name="adjust_master_score"' in run.stdout


def test_a_missing_drbd_resource_is_a_configuration_error(tmp_path: Path) -> None:
    box = Box(tmp_path, [CONNECTED_TO_SECONDARY])
    run = box.run("monitor", OCF_RESKEY_drbd_resource="")
    assert run.returncode == 6
    assert "ocf-exit-reason:drbd_resource is not set" in run.stderr
    assert box.linbit() == []


def test_the_agent_never_masks_a_failure() -> None:
    text = AGENT.read_text(encoding="utf-8")
    assert "|| true" not in text
    assert "2>/dev/null || :" not in text
