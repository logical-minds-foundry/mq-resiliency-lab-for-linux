"""Run lab/scripts/nic-config.sh against stub nmcli/SELinux tools (#1491).

Vagrant's guest network configuration leaves each lab NIC profile labelled
``user_tmp_t`` (written under /tmp, moved into place). NetworkManager can read such a
profile but not replace it, so every ``nmcli connection modify`` of the #866 hardening
failed, and ``|| true`` hid it: on rdqm-rhel@10 no node kept ``autoconnect-priority
100`` / ``autoconnect-retries 0``. The script now relabels the profile directories
before hardening, lets a failed modify fail the script, and reads the values back.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "lab" / "scripts" / "nic-config.sh"

# A one-profile NetworkManager: enp6s0 carries 10.10.1.31. Every call is logged; the
# modify and the read-back values are steered by FAKE_* environment variables.
NMCLI = r"""#!/usr/bin/env bash
echo "nmcli $*" >> "$FAKE_LOG"
case "$*" in
  "connection reload") exit 0 ;;
  "-t -f NAME,TYPE connection show") echo "enp6s0:802-3-ethernet" ;;
  "-g ipv4.addresses connection show enp6s0") echo "10.10.1.31/24" ;;
  "-g connection.interface-name connection show enp6s0") echo "enp6s0" ;;
  "-g connection.autoconnect-priority connection show enp6s0") echo "${FAKE_PRIO:-100}" ;;
  "-g connection.autoconnect-retries connection show enp6s0") echo "${FAKE_RETRIES:-0}" ;;
  "connection modify"*)
    if [ -n "${FAKE_MODIFY_FAIL:-}" ]; then
      echo "Error: Failed to modify connection 'enp6s0': failed to update connection" >&2
      exit 1
    fi ;;
esac
exit 0
"""
SELINUXENABLED = '#!/usr/bin/env bash\nexit "${FAKE_SELINUX:-0}"\n'
RESTORECON = '#!/usr/bin/env bash\necho "restorecon $*" >> "$FAKE_LOG"\n'


def _stub(bindir: Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def lab(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "nmcli", NMCLI)
    _stub(bindir, "selinuxenabled", SELINUXENABLED)
    _stub(bindir, "restorecon", RESTORECON)
    keyfiles = tmp_path / "system-connections"
    keyfiles.mkdir()
    return {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "NM_PROFILE_DIRS": f"{keyfiles} {tmp_path / 'absent-network-scripts'}",
    }


def _run(env: dict[str, str], **fake: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    result = subprocess.run(
        ["bash", str(SCRIPT), "10.10.1.31"],
        env={**env, **fake},
        capture_output=True,
        text=True,
        check=False,
    )
    log = Path(env["FAKE_LOG"])
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


def _first(calls: list[str], prefix: str) -> int:
    return next(i for i, c in enumerate(calls) if c.startswith(prefix))


def test_relabels_existing_profile_dirs_before_hardening(lab: dict[str, str]) -> None:
    result, calls = _run(lab)
    assert result.returncode == 0, result.stderr
    keyfiles = lab["NM_PROFILE_DIRS"].split()[0]
    relabels = [c for c in calls if c.startswith("restorecon")]
    # only the directory that exists; a missing ifcfg dir is not an error
    assert relabels == [f"restorecon -R -v {keyfiles}"]
    assert _first(calls, "restorecon") < _first(calls, "nmcli connection modify")


def test_no_relabel_when_selinux_is_disabled(lab: dict[str, str]) -> None:
    result, calls = _run(lab, FAKE_SELINUX="1")
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith("restorecon")]


def test_failed_modify_fails_the_script(lab: dict[str, str]) -> None:
    result, _ = _run(lab, FAKE_MODIFY_FAIL="1")
    assert result.returncode != 0
    assert "failed to update connection" in result.stderr


@pytest.mark.parametrize(("prio", "retries"), [("-100", "0"), ("100", "1")])
def test_hardening_that_did_not_stick_fails_loud(
    lab: dict[str, str], prio: str, retries: str
) -> None:
    result, _ = _run(lab, FAKE_PRIO=prio, FAKE_RETRIES=retries)
    assert result.returncode != 0
    assert "enp6s0" in result.stderr
    assert f"autoconnect-priority={prio}" in result.stderr
