"""net-cut.sh is the lab's reversible link-cut fault primitive (#1638).

net-down.sh destroys and undefines a libvirt network, and net-up.sh does not reattach
running guests' tap devices, so a drill that cut a network with net-down.sh could not
restore it. net-cut.sh instead sets each guest interface's link down or up with
`virsh domif-setlink`, resolving the interface from `virsh domiflist`, and fails loud
when nothing matches, a setlink fails, or domif-getlink disagrees afterwards.

The script runs for real against a stateful stub `virsh` on PATH: the stub serves
libvirt-10-shaped `domiflist` / `domif-getlink` output from fixture files and keeps
each interface's link state, so no libvirt is needed. The live check is plan 310
Task 8 drill 3 (#1430).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "lab" / "scripts"
NET_CUT = SCRIPTS / "net-cut.sh"
NET_DOWN = SCRIPTS / "net-down.sh"

HEADER = " Interface   Type      Source            Model    MAC\n" + "-" * 66 + "\n"


def _iflist(*rows: tuple[str, str, str]) -> str:
    """libvirt 10 `virsh domiflist` output: a header, a rule, then one row per NIC."""
    body = "".join(f" {tap:<11} network   {net:<17} virtio   {mac}\n" for tap, net, mac in rows)
    return HEADER + body + "\n"


MAC_A1_HB = "52:54:00:aa:01:01"
MAC_A2_HB = "52:54:00:aa:01:02"
MAC_B1_HB = "52:54:00:bb:01:01"
MAC_OTHER = "52:54:00:cc:01:01"

DOMAINS = {
    "lab_pdrbd-a1": _iflist(
        ("vnet0", "vagrant-libvirt", "52:54:00:aa:00:01"),
        ("vnet1", "net-hb-a", MAC_A1_HB),
        ("vnet2", "net-wan", "52:54:00:aa:02:01"),
    ),
    "lab_pdrbd-a2": _iflist(
        ("vnet3", "vagrant-libvirt", "52:54:00:aa:00:02"),
        ("vnet4", "net-hb-a", MAC_A2_HB),
        ("vnet5", "net-wan", "52:54:00:aa:02:02"),
    ),
    "lab_pdrbd-b1": _iflist(
        ("vnet6", "vagrant-libvirt", "52:54:00:bb:00:01"),
        ("vnet7", "net-hb-b", MAC_B1_HB),
        ("vnet8", "net-wan", "52:54:00:bb:02:01"),
    ),
    # Not a lab domain: never touched, even though it sits on net-hb-a.
    "other-vm": _iflist(("vnet9", "net-hb-a", MAC_OTHER)),
    # A lab domain that is defined but shut off.
    "lab_pdrbd-a3": _iflist(("-", "net-hb-a", "52:54:00:aa:01:03")),
}
RUNNING = ["lab_pdrbd-a1", "lab_pdrbd-a2", "lab_pdrbd-b1", "other-vm"]

VIRSH = """\
echo "virsh $*" >> "$FIX/calls.log"
[ "$1" = -c ] && shift 2
cmd=$1; shift
case "$cmd" in
  list)
    [ -f "$FIX/fail-list" ] && { echo "error: list failed" >&2; exit 1; }
    cat "$FIX/running"; echo ;;
  domstate)
    if grep -qx "$1" "$FIX/running"; then echo running; echo
    elif [ -f "$FIX/domiflist/$1" ]; then echo "shut off"; echo
    else echo "error: failed to get domain '$1'" >&2; exit 1; fi ;;
  domiflist)
    [ -f "$FIX/domiflist/$1" ] || { echo "error: failed to get domain '$1'" >&2; exit 1; }
    cat "$FIX/domiflist/$1" ;;
  domif-setlink)
    if [ -f "$FIX/fail-setlink" ] && grep -qx "$1 $2" "$FIX/fail-setlink"; then
      echo "error: Failed to update interface link state" >&2; exit 1
    fi
    echo "$3" > "$FIX/link/$1_$2"
    echo "Device updated successfully" ;;
  domif-getlink)
    [ -f "$FIX/fail-getlink" ] && { echo "error: getlink failed" >&2; exit 1; }
    if [ -f "$FIX/getlink-out" ]; then cat "$FIX/getlink-out"
    elif [ -f "$FIX/link/$1_$2" ]; then echo "$2 $(cat "$FIX/link/$1_$2")"
    else echo "$2 up"; fi ;;
  *) echo "stub virsh: unexpected $cmd" >&2; exit 99 ;;
esac
"""


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    """A fixture dir served by a stub `virsh`: running list, domiflist per domain,
    and per-interface link state under link/."""
    fix = tmp_path / "fix"
    (fix / "domiflist").mkdir(parents=True)
    (fix / "link").mkdir()
    for dom, out in DOMAINS.items():
        (fix / "domiflist" / dom).write_text(out)
    (fix / "running").write_text("\n".join(RUNNING) + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    virsh = bin_dir / "virsh"
    virsh.write_text(f'#!/usr/bin/env bash\nFIX="{fix}"\n' + VIRSH)
    virsh.chmod(virsh.stat().st_mode | stat.S_IXUSR)
    return fix


def _run(fix: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fix.parent / 'bin'}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(NET_CUT), *args], env=env, capture_output=True, text=True, check=False
    )


def _calls(fix: Path) -> list[str]:
    log = fix / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _setlinks(fix: Path) -> list[str]:
    return [c for c in _calls(fix) if " domif-setlink " in c]


def _link(fix: Path, dom: str, mac: str) -> str | None:
    path = fix / "link" / f"{dom}_{mac}"
    return path.read_text().strip() if path.exists() else None


def test_script_is_executable_strict_bash_and_never_masks_failure() -> None:
    assert NET_CUT.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(NET_CUT)], check=False).returncode == 0
    text = NET_CUT.read_text()
    assert "set -euo pipefail" in text
    assert "inherit_errexit" in text
    assert "|| true" not in text


def test_down_then_up_cuts_and_restores_every_running_lab_guest_on_the_net(lab: Path) -> None:
    down = _run(lab, "down", "net-hb-a")

    assert down.returncode == 0, down.stderr
    assert _setlinks(lab) == [
        f"virsh -c qemu:///system domif-setlink lab_pdrbd-a1 {MAC_A1_HB} down",
        f"virsh -c qemu:///system domif-setlink lab_pdrbd-a2 {MAC_A2_HB} down",
    ]
    assert _link(lab, "lab_pdrbd-a1", MAC_A1_HB) == "down"
    assert _link(lab, "lab_pdrbd-a2", MAC_A2_HB) == "down"
    # Other networks, other sites, and non-lab domains are left alone.
    assert _link(lab, "lab_pdrbd-b1", MAC_B1_HB) is None
    assert _link(lab, "other-vm", MAC_OTHER) is None
    # Each virsh action is echoed verbatim, and the verified state reported.
    for dom, mac in (("lab_pdrbd-a1", MAC_A1_HB), ("lab_pdrbd-a2", MAC_A2_HB)):
        assert f"+ virsh -c qemu:///system domif-setlink {dom} {mac} down" in down.stdout
        assert f"+ virsh -c qemu:///system domif-getlink {dom} {mac}" in down.stdout
        assert f"down: {dom} {mac} (net-hb-a)" in down.stdout

    up = _run(lab, "up", "net-hb-a")

    assert up.returncode == 0, up.stderr
    assert _link(lab, "lab_pdrbd-a1", MAC_A1_HB) == "up"
    assert _link(lab, "lab_pdrbd-a2", MAC_A2_HB) == "up"
    assert f"up: lab_pdrbd-a2 {MAC_A2_HB} (net-hb-a)" in up.stdout
    # The network itself is never destroyed or redefined.
    assert not [c for c in _calls(lab) if " net-" in c.split("qemu:///system", 1)[-1]]


def test_wan_cut_covers_every_site(lab: Path) -> None:
    result = _run(lab, "down", "net-wan")

    assert result.returncode == 0, result.stderr
    assert [c.split()[-3] for c in _setlinks(lab)] == [
        "lab_pdrbd-a1",
        "lab_pdrbd-a2",
        "lab_pdrbd-b1",
    ]


def test_named_nodes_limit_the_cut(lab: Path) -> None:
    result = _run(lab, "down", "net-hb-a", "pdrbd-a2")

    assert result.returncode == 0, result.stderr
    assert _setlinks(lab) == [
        f"virsh -c qemu:///system domif-setlink lab_pdrbd-a2 {MAC_A2_HB} down",
    ]
    assert _link(lab, "lab_pdrbd-a1", MAC_A1_HB) is None


def test_a_guest_with_two_nics_on_the_net_gets_both_cut(lab: Path) -> None:
    second = "52:54:00:aa:01:99"
    (lab / "domiflist" / "lab_pdrbd-a1").write_text(
        _iflist(("vnet1", "net-hb-a", MAC_A1_HB), ("vnet10", "net-hb-a", second))
    )

    result = _run(lab, "down", "net-hb-a", "pdrbd-a1")

    assert result.returncode == 0, result.stderr
    assert _link(lab, "lab_pdrbd-a1", MAC_A1_HB) == "down"
    assert _link(lab, "lab_pdrbd-a1", second) == "down"


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("down",),
        ("sideways", "net-hb-a"),
        ("down", ""),
    ],
)
def test_usage_errors_exit_2_without_calling_virsh(lab: Path, args: tuple[str, ...]) -> None:
    result = _run(lab, *args)

    assert result.returncode == 2
    assert "usage: net-cut.sh <down|up> <net-name> [node ...]" in result.stderr
    assert _calls(lab) == []


def test_no_matching_interface_fails_loud(lab: Path) -> None:
    result = _run(lab, "down", "net-nope")

    assert result.returncode == 1
    assert "no running lab domain has an interface on net-nope" in result.stderr
    assert _setlinks(lab) == []


def test_no_running_lab_domain_fails_loud(lab: Path) -> None:
    (lab / "running").write_text("other-vm\n")

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert "no running lab domain has an interface on net-hb-a" in result.stderr
    assert _setlinks(lab) == []


def test_a_named_node_off_the_net_fails_before_any_link_changes(lab: Path) -> None:
    # pdrbd-a1 is resolvable, but pdrbd-b1 has no net-hb-a NIC: nothing is cut.
    result = _run(lab, "down", "net-hb-a", "pdrbd-a1", "pdrbd-b1")

    assert result.returncode == 1
    assert "lab_pdrbd-b1 has no interface on net-hb-a" in result.stderr
    assert _setlinks(lab) == []


def test_a_named_node_that_is_not_running_fails_loud(lab: Path) -> None:
    result = _run(lab, "down", "net-hb-a", "pdrbd-a3")

    assert result.returncode == 1
    assert "lab_pdrbd-a3 is 'shut off', not running" in result.stderr
    assert _setlinks(lab) == []


def test_an_unknown_named_node_fails_loud(lab: Path) -> None:
    result = _run(lab, "down", "net-hb-a", "pdrbd-zz")

    assert result.returncode == 1
    assert "virsh domstate lab_pdrbd-zz failed" in result.stderr
    assert _setlinks(lab) == []


def test_a_failed_list_fails_loud(lab: Path) -> None:
    (lab / "fail-list").write_text("")

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert "virsh list --name failed" in result.stderr


def test_a_failed_domiflist_fails_loud(lab: Path) -> None:
    (lab / "domiflist" / "lab_pdrbd-a2").unlink()

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert "virsh domiflist lab_pdrbd-a2 failed" in result.stderr
    assert _setlinks(lab) == []


@pytest.mark.parametrize(
    ("out", "what"),
    [
        (" Interface   Type   Model\n---\n vnet1 network virtio\n", "header"),
        ("", "header"),
        (HEADER + " vnet1   network   net-hb-a   52:54:00:aa:01:01\n", "row"),
    ],
)
def test_unexpected_domiflist_output_fails_loud(lab: Path, out: str, what: str) -> None:
    (lab / "domiflist" / "lab_pdrbd-a1").write_text(out)

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert f"unexpected domiflist {what} for lab_pdrbd-a1" in result.stderr
    assert _setlinks(lab) == []


def test_a_failed_setlink_fails_loud_and_stops(lab: Path) -> None:
    (lab / "fail-setlink").write_text(f"lab_pdrbd-a1 {MAC_A1_HB}\n")

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert f"domif-setlink lab_pdrbd-a1 {MAC_A1_HB} down failed" in result.stderr
    assert len(_setlinks(lab)) == 1, "no further links are touched after a failure"
    assert _link(lab, "lab_pdrbd-a2", MAC_A2_HB) is None


@pytest.mark.parametrize(
    "out",
    [
        f"{MAC_A1_HB} up\n",  # the link did not change
        f"{MAC_A1_HB} default\n",  # old libvirt's no-state wording
        "",  # nothing at all
        f"{MAC_A1_HB} down\n{MAC_A1_HB} down\n",  # more than one line
        f"{MAC_A1_HB} link down\n",  # an unknown layout
    ],
)
def test_getlink_not_reporting_the_requested_state_fails_loud(lab: Path, out: str) -> None:
    (lab / "getlink-out").write_text(out)

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert f"domif-getlink lab_pdrbd-a1 {MAC_A1_HB} reported" in result.stderr
    assert "expected 'down'" in result.stderr


def test_a_failed_getlink_fails_loud(lab: Path) -> None:
    (lab / "fail-getlink").write_text("")

    result = _run(lab, "down", "net-hb-a")

    assert result.returncode == 1
    assert f"domif-getlink lab_pdrbd-a1 {MAC_A1_HB} failed" in result.stderr


def test_net_down_warns_it_is_not_a_fault_injector() -> None:
    header = NET_DOWN.read_text().split("set -euo pipefail", 1)[0]
    assert "NOT a fault injector" in header
    assert "net-cut.sh" in header
