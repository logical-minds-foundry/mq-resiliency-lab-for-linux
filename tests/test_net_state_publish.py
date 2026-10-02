"""lab_network_state is published on change, not polled (#1253).

The lab's networks only change state when net-up.sh / net-down.sh change them, so
those scripts publish the new state themselves (via net-state-publish.sh), the
observe phase publishes once after creating the drop zone. The old root
`lab-net-state` polling probe is retired from host-obs.yml (see tests/test_host_obs.py).

The scripts are exercised for real against stub `uv` / `sudo` / `virsh` / `id`
binaries on PATH and a temp drop zone (MQLAB_TEXTFILE_DIR), so no libvirt or root is
needed — and the stub `id` makes the non-root (sudo) path run even when the suite
itself runs as root in the validation container.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "lab" / "scripts"
PUBLISH = SCRIPTS / "net-state-publish.sh"

PROM = (
    "# HELP lab_network_state libvirt network state (0=absent,1=inactive,2=active)\n"
    "# TYPE lab_network_state gauge\n"
    'lab_network_state{network="net-hb-a"} 0\n'
    'lab_network_state{network="net-wan"} 2\n'
)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _env(tmp_path: Path, drop: Path) -> dict[str, str]:
    """PATH with stubs: uv (records argv, prints PROM), sudo (drops -n, runs the
    rest), virsh (records argv; `net-info` reports Active: yes), id (a non-root uid)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    prom = tmp_path / "prom.txt"
    prom.write_text(PROM)
    _stub(bin_dir, "uv", f'echo "uv $*" >> "{log}"\ncat "{prom}"\n')
    _stub(bin_dir, "sudo", f'echo "sudo $*" >> "{log}"\n[ "$1" = "-n" ] && shift\nexec "$@"\n')
    _stub(
        bin_dir,
        "virsh",
        f'echo "virsh $*" >> "{log}"\n'
        'case " $* " in *" net-info "*) echo "Active:         yes";; esac\n',
    )
    _stub(bin_dir, "id", 'if [ "$1" = "-u" ]; then echo 1000; else exec /usr/bin/id "$@"; fi\n')
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["MQLAB_TEXTFILE_DIR"] = str(drop)
    return env


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args], env=env, capture_output=True, text=True, check=False
    )


def test_publish_script_is_executable_strict_bash() -> None:
    assert PUBLISH.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(PUBLISH)], check=False).returncode == 0
    assert "set -euo pipefail" in PUBLISH.read_text()


def test_publish_renders_via_uv_run_and_installs_atomically(tmp_path: Path) -> None:
    drop = tmp_path / "textfile"
    drop.mkdir()
    env = _env(tmp_path, drop)

    result = _run(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    assert (drop / "lab_network_state.prom").read_text() == PROM
    assert not (drop / "lab_network_state.prom.tmp").exists(), "must land via .tmp + mv"
    calls = (tmp_path / "calls.log").read_text()
    # Rendered by the tested CLI, invoked the conventional way (#1252) — never by an
    # absolute .venv/bin path, never as root.
    assert f"uv run --quiet --project {REPO_ROOT} mqlab obs net-state" in calls
    assert ".venv/bin" not in PUBLISH.read_text()
    # Only the install into the root-owned drop zone is privileged, non-interactively.
    assert "sudo -n install" in calls
    assert "sudo -n mv" in calls
    assert "published 2 net(s)" in result.stdout


def test_publish_says_so_and_exits_zero_before_the_drop_zone_exists(tmp_path: Path) -> None:
    # A fresh host's bring-up runs net-up.sh before observe installs node_exporter.
    drop = tmp_path / "textfile"  # deliberately absent
    env = _env(tmp_path, drop)

    result = _run(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    assert "not published" in result.stdout
    assert not drop.exists()
    assert not (tmp_path / "calls.log").exists(), "nothing rendered or installed"


def test_publish_fails_loud_when_rendering_fails(tmp_path: Path) -> None:
    drop = tmp_path / "textfile"
    drop.mkdir()
    env = _env(tmp_path, drop)
    _stub(tmp_path / "bin", "uv", "echo boom >&2\nexit 3\n")

    result = _run(PUBLISH, env)

    assert result.returncode != 0
    assert not (drop / "lab_network_state.prom").exists()


def test_net_down_publishes_after_changing_state(tmp_path: Path) -> None:
    drop = tmp_path / "textfile"
    drop.mkdir()
    env = _env(tmp_path, drop)

    result = _run(SCRIPTS / "net-down.sh", env, "net-hb-a")

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    destroy = next(i for i, c in enumerate(calls) if "net-destroy net-hb-a" in c)
    render = next(i for i, c in enumerate(calls) if c.startswith("uv run"))
    assert destroy < render, "publish must follow the state change"
    assert (drop / "lab_network_state.prom").read_text() == PROM


def test_net_up_publishes_after_changing_state(tmp_path: Path) -> None:
    drop = tmp_path / "textfile"
    drop.mkdir()
    env = _env(tmp_path, drop)

    result = _run(SCRIPTS / "net-up.sh", env, "net-hb-a")

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    autostart = next(i for i, c in enumerate(calls) if "net-autostart net-hb-a" in c)
    render = next(i for i, c in enumerate(calls) if c.startswith("uv run"))
    assert autostart < render, "publish must follow the state change"
    assert (drop / "lab_network_state.prom").read_text() == PROM
