"""Behaviour: the shared Native HA Active lookup fails loud, in itself (#1540).

``ansible/tasks/nativeha-find-active.yml`` polls ``dspmq -o nativeha -x`` until a member of
the group reports ``ROLE(Active)``. It used to set ``failed_when: false`` on that poll, and
Ansible lets ``failed_when`` decide the final status even after an ``until`` loop runs out, so
a group with no Active made the poll SUCCEED and the next task die on Jinja's
``No first item, sequence was empty``. These tests drive the real task file through a local
``ansible-playbook`` run, with a fake ``su`` on ``PATH`` replaying scripted ``dspmq`` answers:

- an Active on the first poll is recorded as ``active_host`` on every host of the play;
- a transient non-zero dspmq, then a poll with no Active, then an Active, still passes;
- no Active within the polls fails in the lookup, naming the queue manager, the group, the
  poll count and dspmq's last rc/stdout/stderr, and ``active_host`` is never recorded.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

DETECT = Path(__file__).resolve().parents[1] / "ansible" / "tasks" / "nativeha-find-active.yml"
ANSIBLE_PLAYBOOK = Path(sys.executable).parent / "ansible-playbook"
GROUP = "nha_test_a"
QM = "QMTEST"
HOSTS = ("nha-t1", "nha-t2", "nha-t3")

# `dspmq -m QMTEST -o nativeha -x` as run on nha-t1: the summary line carries nha-t1's OWN
# role; the indented detail lines list every instance (#722).
REPLICAS = (
    "QMNAME(QMTEST) ROLE(Replica) INSTANCE(nha-t1) INSYNC(yes) QUORUM(2/3)\n"
    " INSTANCE(nha-t1) ROLE(Replica) REPLADDR(10.0.0.1) CONNACTV(yes)\n"
    " INSTANCE(nha-t2) ROLE(Replica) REPLADDR(10.0.0.2) CONNACTV(yes)\n"
    " INSTANCE(nha-t3) ROLE(Unknown) REPLADDR(10.0.0.3) CONNACTV(no)\n"
)
ACTIVE_ON_T2 = (
    "QMNAME(QMTEST) ROLE(Replica) INSTANCE(nha-t1) INSYNC(yes) QUORUM(3/3)\n"
    " INSTANCE(nha-t1) ROLE(Replica) REPLADDR(10.0.0.1) CONNACTV(yes)\n"
    " INSTANCE(nha-t2) ROLE(Active) REPLADDR(10.0.0.2) CONNACTV(yes)\n"
    " INSTANCE(nha-t3) ROLE(Replica) REPLADDR(10.0.0.3) CONNACTV(yes)\n"
)
# An Active that is not a member of the group never counts (no stray delegation target).
STRAY_ACTIVE = REPLICAS + " INSTANCE(elsewhere) ROLE(Active) REPLADDR(10.9.9.9) CONNACTV(yes)\n"
NOT_RUNNING = ("", "AMQ8146E: IBM MQ queue manager not available.\n", 72)

FAKE_SU = """#!/bin/sh
# Fake `su - mqm -c <cmd>`: replay the next scripted dspmq answer, repeating the last.
dir=$(dirname "$0")
n=$(cat "$dir/count")
n=$((n + 1))
echo "$n" > "$dir/count"
printf '%s\\n' "$*" >> "$dir/calls"
i=$n
[ -e "$dir/answer.$i.rc" ] || i=$(cat "$dir/last")
cat "$dir/answer.$i.out"
cat "$dir/answer.$i.err" >&2
exit "$(cat "$dir/answer.$i.rc")"
"""


def _run(
    tmp_path: Path, answers: list[tuple[str, str, int]], retries: int
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for i, (out, err, rc) in enumerate(answers, start=1):
        (fake / f"answer.{i}.out").write_text(out)
        (fake / f"answer.{i}.err").write_text(err)
        (fake / f"answer.{i}.rc").write_text(str(rc))
    (fake / "last").write_text(str(len(answers)))
    (fake / "count").write_text("0")
    (fake / "calls").write_text("")
    (fake / "su").write_text(FAKE_SU)
    (fake / "su").chmod(0o755)

    (tmp_path / "inventory.ini").write_text(
        f"[{GROUP}]\n"
        + "".join(
            f"{h} ansible_connection=local ansible_python_interpreter={sys.executable}\n"
            for h in HOSTS
        )
    )
    (tmp_path / "play.yml").write_text(
        f"""- hosts: {GROUP}
  gather_facts: false
  tasks:
    - ansible.builtin.include_tasks: {DETECT}
      vars: {{ nha_group: {GROUP}, nha_qm: {QM}, nha_find_retries: {retries}, nha_find_delay: 0 }}
    - ansible.builtin.copy:
        content: "{{{{ active_host }}}}"
        dest: "{tmp_path}/active.{{{{ inventory_hostname }}}}"
        mode: "0644"
"""
    )
    env = {
        **os.environ,
        "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
        "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
        "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp"),
        "ANSIBLE_REMOTE_TEMP": str(tmp_path / "ansible-tmp"),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_RETRY_FILES_ENABLED": "0",
    }
    env.pop("ANSIBLE_CONFIG", None)
    result = subprocess.run(  # noqa: S603 — fixed argv, our own task file
        [str(ANSIBLE_PLAYBOOK), "-i", "inventory.ini", "play.yml"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return result, fake


def _calls(fake: Path) -> list[str]:
    return (fake / "calls").read_text().splitlines()


def _recorded(tmp_path: Path) -> dict[str, str]:
    return {h: (tmp_path / f"active.{h}").read_text() for h in HOSTS if (tmp_path / f"active.{h}").exists()}


def test_an_active_on_the_first_poll_is_broadcast_to_every_host(tmp_path: Path) -> None:
    result, fake = _run(tmp_path, [(ACTIVE_ON_T2, "", 0)], retries=3)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _recorded(tmp_path) == dict.fromkeys(HOSTS, "nha-t2")
    # One poll, run once for the play, as mqm, against the named queue manager.
    assert _calls(fake) == [f"- mqm -c /opt/mqm/bin/dspmq -m {QM} -o nativeha -x"]


def test_a_transient_failure_then_an_active_still_passes(tmp_path: Path) -> None:
    answers = [NOT_RUNNING, (REPLICAS, "", 0), (STRAY_ACTIVE, "", 0), (ACTIVE_ON_T2, "", 0)]
    result, fake = _run(tmp_path, answers, retries=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _recorded(tmp_path) == dict.fromkeys(HOSTS, "nha-t2")
    assert len(_calls(fake)) == 4


@pytest.mark.parametrize(
    ("last", "rc_text", "stdout_text", "stderr_text"),
    [
        (NOT_RUNNING, "rc=72", "stdout: (empty)", "stderr: AMQ8146E: IBM MQ queue manager"),
        (
            (STRAY_ACTIVE, "", 0),
            "rc=0",
            "INSTANCE(elsewhere) ROLE(Active)",
            "stderr: (empty)",
        ),
    ],
    ids=["dspmq-failing", "no-member-active"],
)
def test_no_active_fails_in_the_lookup_with_the_cause(
    tmp_path: Path,
    last: tuple[str, str, int],
    rc_text: str,
    stdout_text: str,
    stderr_text: str,
) -> None:
    result, fake = _run(tmp_path, [(REPLICAS, "", 0), last], retries=2)
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    # retries: 2 is the first poll plus two retries, and the message says so.
    assert len(_calls(fake)) == 3
    assert f"fail loud unless {QM} has an Active instance in {GROUP}" in output
    message = " ".join(output.split())
    assert f"No Active instance of {QM} in {GROUP} after 3 polls, 0s apart" in message
    assert f"dspmq -m {QM} -o nativeha -x` on nha-t1 never listed a member" in message
    assert f"Last dspmq {rc_text};" in message
    assert stdout_text in message
    assert stderr_text in message
    # The record task is unreachable without an Active: no template error, nothing recorded.
    assert "record the active instance" not in output
    assert "No first item" not in output
    assert _recorded(tmp_path) == {}
