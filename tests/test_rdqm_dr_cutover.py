"""Contract + parser tests for lab/scripts/rdqm-dr-cutover.sh (#294 hardening).

The RDQM DR cutover script is not safely sourceable (it `cd`s into ansible/ and runs a
live flow at load), so — following the repo's bash-test convention (tests/test_setup_script.py)
— we assert its contract via `bash -n` + text greps, and exercise the one pure, load-bearing
helper (`rdqm_field`, the rdqmstatus field parser that all three findings depend on) by
extracting just that function and running it against real captured rdqmstatus output.
"""

from __future__ import annotations

import stat
import subprocess
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "lab" / "scripts" / "rdqm-dr-cutover.sh"

# Real `rdqmstatus -m RDQMAPP` captures from the live rdqm-rhel arm (2026-07-31).
# A secondary node's local block (HA current location NAMES the primary; DR status defers).
SAMPLE_SECONDARY = textwrap.dedent(
    """\
    Node:                                   rdqm-a2
    Queue manager status:                   Running elsewhere
    HA role:                                Secondary
    HA status:                              Normal
    HA current location:                    rdqm-a1
    HA preferred location:                  rdqm-a1
    HA blocked location:                    None
    DR role:                                Primary
    DR status:                              See rdqm-a1
    """
)
# The HA primary's local block ("This node"), DR in-sync.
SAMPLE_PRIMARY = textwrap.dedent(
    """\
    Node:                                   rdqm-a1
    Queue manager status:                   Running
    HA current location:                    This node
    HA preferred location:                  This node
    HA blocked location:                    None
    DR role:                                Primary
    DR status:                              Normal
    """
)


def _extract_rdqm_field() -> str:
    """Pull just the `rdqm_field` function block out of the script (ends at a lone `}`)."""
    lines = SCRIPT.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("rdqm_field()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


def _run_field(label: str, sample: str) -> str:
    harness = _extract_rdqm_field() + f'\nrdqm_field "{label}" "$(cat)"\n'
    result = subprocess.run(["bash", "-c", harness], input=sample, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# --- contract: script shape -------------------------------------------------


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file(), "rdqm-dr-cutover.sh is missing"
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, "rdqm-dr-cutover.sh is not executable"


def test_script_is_valid_bash() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_script_is_strict() -> None:
    assert "set -euo pipefail" in SCRIPT.read_text(), "script must fail loud"


# --- finding 1: HA-primary discovery, not hardcoded node-1 ------------------


def test_discovers_ha_primary_from_rdqmstatus() -> None:
    text = SCRIPT.read_text()
    assert "HA current location" in text, "must resolve the primary from rdqmstatus"
    assert "ha_primary()" in text, "must define an HA-primary resolver"
    # rdqmdr must target the RESOLVED primary, never a hardcoded node.
    assert "rdqmdr -m $QM -s" in text and "rdqmdr -m $QM -p" in text
    assert 'run "$FROM_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -s"' in text
    assert 'run "$TO_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -p"' in text


def test_no_hardcoded_node1_for_rdqmdr() -> None:
    # The #288 bug: rdqmdr issued on rdqm-a1/rdqm-b1 regardless of the real HA primary.
    text = SCRIPT.read_text()
    assert "rdqm-a1" not in text.replace("rdqm-a1 rdqm-a2 rdqm-a3", ""), (
        "rdqm-a1 must appear only inside the site node LIST, never as an rdqmdr target"
    )
    assert "rdqm-b1" not in text.replace("rdqm-b1 rdqm-b2 rdqm-b3", ""), (
        "rdqm-b1 must appear only inside the site node LIST, never as an rdqmdr target"
    )


def test_reresolves_primary_after_promote() -> None:
    # The verify/poll must follow the QM if the promote bounce relocates it (finding 1, part 2).
    text = SCRIPT.read_text()
    promote = text.index("rdqmdr -m $QM -p")
    verify = text.index("=== 6. Verify")
    assert 'TO_PRIMARY="$(ha_primary "${TO_NODES[@]}")"' in text[promote:verify], (
        "must re-resolve the TO-site HA primary after the promote"
    )


# --- finding 2: pre-cut DR-in-sync gate -------------------------------------


def test_dr_in_sync_gate_before_cut() -> None:
    text = SCRIPT.read_text()
    assert "confirm_dr_in_sync" in text, "must gate on DR sync before cutting"
    assert "DR status" in text and "Normal" in text
    assert "DR_SYNC_TIMEOUT" in text, "the sync gate must be time-bounded"
    # The gate must run BEFORE the demote/promote.
    gate = text.index('confirm_dr_in_sync "$FROM_PRIMARY"')
    demote = text.index('run "$FROM_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -s"')
    assert gate < demote, "DR-in-sync gate must precede the cut"


# --- finding 3: bounded post-promote HA-settle wait -------------------------


def test_ha_settle_wait_is_bounded() -> None:
    text = SCRIPT.read_text()
    assert "wait_ha_settle" in text, "must wait for the post-promote HA bounce to settle"
    assert "HA blocked location" in text, "must detect the TCG blocked-location artifact"
    assert "HA_SETTLE_TIMEOUT" in text, "the HA-settle wait must be time-bounded"


# --- parser behavior (the load-bearing helper) ------------------------------


def test_rdqm_field_reads_named_primary_from_secondary_block() -> None:
    assert _run_field("HA current location", SAMPLE_SECONDARY) == "rdqm-a1"


def test_rdqm_field_reads_this_node_on_primary() -> None:
    assert _run_field("HA current location", SAMPLE_PRIMARY) == "This node"


def test_rdqm_field_does_not_confuse_current_with_preferred_location() -> None:
    # "HA current location" must not match "HA preferred location" (anchored at line start).
    assert _run_field("HA current location", SAMPLE_SECONDARY) == "rdqm-a1"


def test_rdqm_field_reads_dr_status() -> None:
    assert _run_field("DR status", SAMPLE_PRIMARY) == "Normal"
    assert _run_field("DR status", SAMPLE_SECONDARY) == "See rdqm-a1"


def test_rdqm_field_reads_blocked_location_and_qm_status() -> None:
    assert _run_field("HA blocked location", SAMPLE_PRIMARY) == "None"
    assert _run_field("Queue manager status", SAMPLE_PRIMARY) == "Running"
