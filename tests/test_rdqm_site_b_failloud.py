"""The rdqm-rhel provisioning and DR scripts neither assume site A silently nor swallow
failures (#1625, found by #1622).

* Provisioning configures the QM at site A. With the QM live at site B after a cutover, both
  rdqm-qm-create.sh and the rdqm-active-node role refuse, naming `mqlab dr failback rdqm-rhel`.
* rdqm-dr-cutover.sh no longer runs `rdqmint ... || true`: it checks the site's floating IP
  first, as add_vip does (#583), and step 6 fails the run unless the new live site is the DR
  primary with the QM Running on its own VIP.
* rdqm-qm-create.sh's closing site-B status call is no longer masked.

The scripts `cd` into ansible/ and drive a live lab, so they are exercised with a stub
`ansible` that answers with the captured RDQM blueprint (docs/reference/rdqm-blueprint/).
The live check runs with plan 310 Task 8 on the x86 CloudLab.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "lab" / "scripts"
CREATE = SCRIPTS / "rdqm-qm-create.sh"
CUTOVER = SCRIPTS / "rdqm-dr-cutover.sh"
ROLE = REPO_ROOT / "ansible" / "roles" / "rdqm-active-node" / "tasks" / "main.yml"
BLUEPRINT = REPO_ROOT / "docs" / "reference" / "rdqm-blueprint"
FAILBACK = "mqlab dr failback rdqm-rhel"


def _capture(host: str) -> str:
    return (BLUEPRINT / host / "rdqmstatus-qm.txt").read_text(encoding="utf-8")


def _function(script: Path, name: str) -> str:
    """One function block of a script: from `name() {` to its lone closing `}`."""
    lines = script.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{name}()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


# --- rdqm-qm-create.sh: refuse while site B is live -------------------------------------


def _run_require(tmp_path: Path, a_status: str | None) -> subprocess.CompletedProcess[str]:
    """Run require_site_a_dr_primary with `ansible` answering every site-A node with
    a_status (None: rdqmstatus fails everywhere) and `sleep` a no-op."""
    status = tmp_path / "status.txt"
    if a_status is not None:
        status.write_text(a_status, encoding="utf-8")
    stub = (
        "sleep() { :; }\n"
        "ansible() {\n"
        f'  [ -f "{status}" ] || {{ echo "$1 | FAILED | rc=1 >>"; return 2; }}\n'
        '  echo "$1 | CHANGED | rc=0 >>"\n'
        f'  cat "{status}"\n'
        "}\n"
    )
    harness = "\n".join(
        [
            "set -euo pipefail",
            'QM="RDQMAPP"',
            stub,
            _function(CREATE, "site_a_dr_role"),
            _function(CREATE, "require_site_a_dr_primary"),
            "require_site_a_dr_primary",
        ]
    )
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False)


def test_create_proceeds_when_site_a_is_the_dr_primary(tmp_path: Path) -> None:
    # The HA primary's block and a secondary's both carry the site's DR role.
    for host in ("rdqm-a1", "rdqm-a2"):
        result = _run_require(tmp_path, _capture(host))
        assert result.returncode == 0, result.stderr
        assert "site A is the DR primary" in result.stdout


def test_create_refuses_when_site_a_is_the_dr_secondary(tmp_path: Path) -> None:
    # After a cutover site A reports what site B reports in the blueprint: DR role Secondary.
    result = _run_require(tmp_path, _capture("rdqm-b1"))
    assert result.returncode == 1
    assert "live at site B" in result.stderr
    assert FAILBACK in result.stderr


def test_create_fails_loud_on_an_unexpected_dr_role(tmp_path: Path) -> None:
    result = _run_require(tmp_path, "DR role:                                Unknown\n")
    assert result.returncode == 1
    assert "unexpected DR role 'Unknown'" in result.stderr


def test_create_fails_loud_when_no_site_a_node_answers(tmp_path: Path) -> None:
    result = _run_require(tmp_path, None)
    assert result.returncode == 1
    assert "could not read the DR role" in result.stderr


def test_create_checks_the_dr_role_before_touching_an_existing_qm() -> None:
    text = CREATE.read_text(encoding="utf-8")
    dr_branch = text[text.index('if [ "$DR" = 1 ]; then\n  echo "=== HA/DR detected') :]
    skip = dr_branch.index("already exists on rdqm-a1 — skipping create")
    guard = dr_branch.index("    require_site_a_dr_primary\n")
    first_use = dr_branch.index("A_PRIMARY=$(active_a_node)")
    assert skip < guard < first_use


def test_create_does_not_mask_the_site_b_status_call() -> None:
    text = CREATE.read_text(encoding="utf-8")
    assert '[ "$DR" = 1 ] && run' not in text
    assert 'if [ "$DR" = 1 ]; then\n  run rdqm-b1 "/opt/mqm/bin/rdqmstatus -m $QM"\nfi' in text


# --- rdqm-active-node role: refuse while site B is live ---------------------------------


def _role_tasks() -> list[dict]:
    return yaml.safe_load(ROLE.read_text(encoding="utf-8"))


def test_role_refuses_before_the_running_wait() -> None:
    tasks = _role_tasks()
    names = [t["name"] for t in tasks]
    refuse = next(i for i, n in enumerate(names) if n.startswith("refuse while"))
    wait = next(i for i, n in enumerate(names) if n.startswith("read local"))
    assert refuse < wait
    read = tasks[refuse - 1]
    assert read["ansible.builtin.command"] == "/opt/mqm/bin/rdqmstatus -m {{ rdqm_qm }}"
    assert read["until"] == "_rdqm_status.rc == 0"
    assert FAILBACK in tasks[refuse]["ansible.builtin.assert"]["fail_msg"]


def test_role_secondary_pattern_matches_only_a_dr_secondary() -> None:
    # Jinja's `search(..., multiline=True)` is re.search with re.MULTILINE.
    [task] = [t for t in _role_tasks() if t["name"].startswith("refuse while")]
    assert "multiline=True" in task["ansible.builtin.assert"]["that"]
    pattern = re.compile(task["vars"]["_rdqm_dr_secondary_re"], re.MULTILINE)
    for host in ("rdqm-b1", "rdqm-b2", "rdqm-b3"):
        assert pattern.search(_capture(host)), host
    for host in ("rdqm-a1", "rdqm-a2", "rdqm-a3"):
        assert not pattern.search(_capture(host)), host
    # An HA-only QM has no DR lines and passes.
    ha_only = "\n".join(ln for ln in _capture("rdqm-a1").splitlines() if not ln.startswith("DR "))
    assert not pattern.search(ha_only)


# --- rdqm-dr-cutover.sh: no swallowed VIP add, a verify that cannot pass falsely ---------

# Stub `ansible`: answers rdqmstatus from the blueprint, flipping the DR roles once
# `rdqmdr -p` runs; logs every command. FAKE_* env vars bend the post-cutover answer.
_FAKE_ANSIBLE = r"""#!/usr/bin/env bash
host="$1"; cmd="${@: -1}"
echo "$host :: $cmd" >> "$FAKE_LOG"
case "$cmd" in
  *"rdqmdr -m"*"-p"*) touch "$FAKE_STATE/promoted"; echo "$host | CHANGED | rc=0 >>"; exit 0 ;;
  *"rdqmdr -m"*) echo "$host | CHANGED | rc=0 >>"; exit 0 ;;
  *"ip -br addr"*) echo "$host | CHANGED | rc=0 >>"; echo "enp6s0"; exit 0 ;;
  *rdqmint*)
    if [ "${FAKE_RDQMINT_RC:-0}" != 0 ]; then
      echo "$host | FAILED | rc=$FAKE_RDQMINT_RC >>"; echo "AMQ3873E: add failed"; exit 2
    fi
    echo "$host | CHANGED | rc=0 >>"; exit 0 ;;
  *"rdqmstatus -m"*)
    echo "$host | CHANGED | rc=0 >>"
    if [ -f "$FAKE_STATE/promoted" ]; then
      case "$host" in
        rdqm-b*) sed -e "s/^Queue manager status:.*/Queue manager status: ${FAKE_B_QM}/" \
                     -e "s/^DR role:.*/DR role: Primary/" \
                     -e "s/^HA floating IP address:.*/HA floating IP address: ${FAKE_B_FIP}/" \
                     "$FAKE_BP/rdqm-b1/rdqmstatus-qm.txt" ;;
        *) sed -e "s/^Queue manager status:.*/Queue manager status:   Ended immediately/" \
               -e "s/^DR role:.*/DR role:                Secondary/" \
               "$FAKE_BP/rdqm-a1/rdqmstatus-qm.txt" ;;
      esac
    else
      case "$host" in
        rdqm-b*) cat "$FAKE_BP/rdqm-b1/rdqmstatus-qm.txt" ;;
        *) cat "$FAKE_BP/rdqm-a1/rdqmstatus-qm.txt" ;;
      esac
    fi
    exit 0 ;;
esac
echo "$host | FAILED | rc=1 >> unexpected command: $cmd"; exit 2
"""


def _cutover(
    tmp_path: Path, *, b_fip: str = "10.10.2.100", b_qm: str = "Running", rdqmint_rc: int = 0
) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "ansible"
    fake.write_text(_FAKE_ANSIBLE, encoding="utf-8")
    fake.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "ansible.log"
    log.touch()
    env = {
        **os.environ,
        # The stub first; then this interpreter's bin dir, whose python3 has PyYAML for
        # the script's topology.yaml VIP lookup.
        "PATH": f"{bindir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_STATE": str(state),
        "FAKE_BP": str(BLUEPRINT),
        "FAKE_B_FIP": b_fip,
        "FAKE_B_QM": b_qm,
        "FAKE_RDQMINT_RC": str(rdqmint_rc),
        "HA_SETTLE_TIMEOUT": "0",
        "DR_SYNC_TIMEOUT": "0",
    }
    result = subprocess.run(
        ["bash", str(CUTOVER), "a2b", "RDQMAPP"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    return result, log.read_text(encoding="utf-8")


def test_cutover_skips_the_add_when_the_site_already_has_its_vip(tmp_path: Path) -> None:
    result, log = _cutover(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "already configured" in result.stdout
    assert "rdqmint" not in log
    assert "cutover a2b complete" in result.stdout


def test_cutover_adds_a_missing_vip_without_masking_it(tmp_path: Path) -> None:
    # No FIP at the new live site; the add succeeds, so step 6 would see none -> it fails
    # (the stub does not model the add). What matters: rdqmint ran bare, no `|| true`.
    result, log = _cutover(tmp_path, b_fip="")
    assert "rdqm-b1 :: /opt/mqm/bin/rdqmint -m RDQMAPP -a -f 10.10.2.100 -l enp6s0\n" in log
    assert "|| true" not in log
    assert result.returncode == 1


def test_cutover_fails_loud_when_the_vip_add_fails(tmp_path: Path) -> None:
    result, log = _cutover(tmp_path, b_fip="", rdqmint_rc=2)
    assert result.returncode != 0
    assert "rdqmint" in log
    assert "=== 6. Verify" not in result.stdout


def test_cutover_fails_loud_on_a_foreign_vip(tmp_path: Path) -> None:
    result, log = _cutover(tmp_path, b_fip="10.10.2.99")
    assert result.returncode == 1
    assert "carries floating IP 10.10.2.99, not the site's 10.10.2.100" in result.stderr
    assert "rdqmint" not in log


@pytest.mark.parametrize("qm_status", ["Ended immediately", "Starting"])
def test_cutover_verify_fails_unless_the_qm_runs_at_the_new_site(
    tmp_path: Path, qm_status: str
) -> None:
    result, _ = _cutover(tmp_path, b_qm=qm_status)
    assert result.returncode == 1
    assert f"'Queue manager status: {qm_status}', expected 'Running'" in result.stderr
    assert "cutover a2b complete" not in result.stdout


def test_cutover_has_no_blanket_or_true_on_a_command() -> None:
    # rdqmstatus_on's `|| true` remains: it feeds bounded retry loops that fail loud.
    lines = [
        ln.strip()
        for ln in CUTOVER.read_text(encoding="utf-8").splitlines()
        if "|| true" in ln and not ln.lstrip().startswith("#")
    ]
    rdqmstatus_on = 'ansible "$1" -b -m shell -a "/opt/mqm/bin/rdqmstatus -m $QM" 2>/dev/null'
    assert lines == [f"{rdqmstatus_on} || true"]
