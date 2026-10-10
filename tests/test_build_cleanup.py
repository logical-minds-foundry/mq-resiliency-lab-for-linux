"""Behavioral tests for lab/boxes/_build-cleanup.sh (#1404).

Before this, a bake that failed after its build domain started exited with the guest
still RUNNING and its disk, console log and scratch files left behind until the next run
of that box (seen on #1287, #1332, #1413). The helper is an EXIT trap the builders arm
once the domain is named. On a non-zero exit it keeps the console log as evidence,
destroys and undefines the domain, removes every registered scratch path, and exits with
the ORIGINAL code. On exit 0 it does nothing.

Run with stubbed ``virsh`` and ``sudo`` (pass-through) on PATH, so no libvirt or root is
needed. Modelled on tests/test_await_install.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BOXES = Path(__file__).resolve().parents[1] / "lab" / "boxes"
HELPER = BOXES / "_build-cleanup.sh"

_SUDO = '#!/usr/bin/env bash\nexec "$@"\n'

# Logs every call. `dominfo` succeeds iff the domain "exists" ($DOM_EXISTS=1); `domstate`
# prints $DOM_STATE; destroy/undefine exit $VIRSH_RC (default 0).
_VIRSH = """#!/usr/bin/env bash
echo "$*" >> "$VIRSH_LOG"
case "$3" in
  dominfo) [ "${DOM_EXISTS:-1}" = 1 ] ;;
  domstate) echo "${DOM_STATE:-running}" ;;
  destroy|undefine) exit "${VIRSH_RC:-0}" ;;
  *) echo "unexpected virsh call: $*" >&2; exit 99 ;;
esac
"""

# A miniature builder: arm the trap the way the real ones do, create scratch, then end
# with $END (a command; `exit 3` or `false` under set -e, or `true` for success).
_DRIVER = """set -euo pipefail
. "{helper}"
BUILD_DOM=fatbox-x-build
CONSOLE="{tmp}/pool/fatbox-x-build-console.log"
BUILD_EVIDENCE_DIR="{tmp}/evidence"
trap build_cleanup_on_exit EXIT
build_cleanup_add "{tmp}/pool/fatbox-x-build.qcow2" "{tmp}/work"
eval "$END"
"""


def _run(
    tmp_path: Path, end: str, **env_extra: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("sudo", _SUDO), ("virsh", _VIRSH)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "fatbox-x-build.qcow2").write_text("disk")
    (pool / "fatbox-x-build-console.log").write_text("anaconda: the last line before it died\n")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "box.img.tmp").write_text("partial")
    log = tmp_path / "virsh.log"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "VIRSH_LOG": str(log),
        "END": end,
        **env_extra,
    }
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", "-c", _DRIVER.format(helper=HELPER, tmp=tmp_path)],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def _virsh_verbs(calls: list[str]) -> list[str]:
    return [c.split()[2] for c in calls]


def test_failure_tears_down_keeps_evidence_and_preserves_exit_code(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "exit 3")
    assert proc.returncode == 3, proc.stderr
    assert _virsh_verbs(calls) == ["dominfo", "domstate", "destroy", "undefine"]
    assert calls[-1].endswith("undefine fatbox-x-build --nvram")
    assert not (tmp_path / "pool" / "fatbox-x-build.qcow2").exists()
    assert not (tmp_path / "pool" / "fatbox-x-build-console.log").exists()
    assert not (tmp_path / "work").exists()
    kept = list((tmp_path / "evidence").glob("fatbox-x-build-console-*.log"))
    assert len(kept) == 1
    assert "the last line before it died" in kept[0].read_text()
    assert "kept the build console log" in proc.stderr


def test_set_e_failure_is_cleaned_up_too(tmp_path: Path) -> None:
    # The real failure mode: a command fails under `set -e` (an ansible bake error).
    proc, calls = _run(tmp_path, "false")
    assert proc.returncode == 1
    assert "destroy" in _virsh_verbs(calls)
    assert not (tmp_path / "pool" / "fatbox-x-build.qcow2").exists()


def test_success_path_is_untouched(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "true")
    assert proc.returncode == 0, proc.stderr
    assert calls == []
    assert (tmp_path / "pool" / "fatbox-x-build.qcow2").exists()
    assert (tmp_path / "work").exists()
    assert not (tmp_path / "evidence").exists()


def test_shut_off_domain_is_undefined_not_destroyed(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "exit 2", DOM_STATE="shut off")
    assert proc.returncode == 2
    assert _virsh_verbs(calls) == ["dominfo", "domstate", "undefine"]


def test_failure_before_the_domain_exists_still_removes_scratch(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "exit 4", DOM_EXISTS="0")
    assert proc.returncode == 4
    assert _virsh_verbs(calls) == ["dominfo"]
    assert not (tmp_path / "pool" / "fatbox-x-build.qcow2").exists()


def test_a_failed_teardown_step_warns_and_keeps_the_original_code(tmp_path: Path) -> None:
    proc, _calls = _run(tmp_path, "exit 5", VIRSH_RC="1")
    assert proc.returncode == 5
    assert "WARNING: bake-failure cleanup: virsh destroy fatbox-x-build failed" in proc.stderr
    assert "WARNING: bake-failure cleanup: virsh undefine fatbox-x-build failed" in proc.stderr
    assert not (tmp_path / "pool" / "fatbox-x-build.qcow2").exists()  # the rest still ran


def test_both_builders_arm_the_cleanup_trap() -> None:
    for builder in (BOXES / "build-fatbox.sh", BOXES / "rhel" / "build-box.sh"):
        lines = [ln.strip() for ln in builder.read_text().splitlines()]
        code = [ln for ln in lines if not ln.startswith("#")]
        assert any(ln.endswith("_build-cleanup.sh") and ln.startswith(".") for ln in code), builder
        assert "trap build_cleanup_on_exit EXIT" in code, builder
        assert any(ln.startswith("BUILD_EVIDENCE_DIR=") for ln in code), builder
        assert any(ln.startswith("build_cleanup_add ") for ln in code), builder
