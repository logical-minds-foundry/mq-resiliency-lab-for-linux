"""lab/boxes/rhel/build-box.sh must require its catalog inputs (--major/--point/--iso,
#1274) and --domain-type/--cpu-mode, and die loudly when one is missing — the
orchestrator always supplies them; a hand-run is told what to pass (#327, design
D3/D5). The arg-validation path runs before any git/virsh/cache side effect, so it
needs no libvirt; the --dry-run decision is driven at a tmp cache via LAB_BOX_CACHE_DIR."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "lab" / "boxes" / "rhel" / "build-box.sh"
_CATALOG = ["--major", "9", "--point", "9.6", "--iso", "rhel-9.6-x86_64-dvd.iso"]
_VIRT = ["--domain-type", "kvm", "--cpu-mode", "host-passthrough"]


def _run(*args: str, cache_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = None if cache_dir is None else {**os.environ, "LAB_BOX_CACHE_DIR": str(cache_dir)}
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_no_args_dies_with_usage():
    result = _run()
    assert result.returncode == 2
    assert "--major is required" in result.stderr
    assert "--domain-type" in result.stderr  # the usage tells a hand-run what to pass


@pytest.mark.parametrize("flag", ["--major", "--point", "--iso"])
def test_each_catalog_flag_is_required(flag):
    i = _CATALOG.index(flag)
    result = _run(*_CATALOG[:i], *_CATALOG[i + 2 :], *_VIRT)
    assert result.returncode == 2
    assert f"ERROR: {flag} is required" in result.stderr


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--major", "nine", "--major must be a number"),
        ("--point", "8.10", "--point must be a 9.x release"),
        ("--iso", "/abs/dvd.iso", "--iso is a filename under build/state/"),
    ],
)
def test_invalid_catalog_values_die(flag, value, message):
    args = list(_CATALOG)
    args[args.index(flag) + 1] = value
    result = _run(*args, *_VIRT)
    assert result.returncode == 2
    assert message in result.stderr


def test_missing_cpu_mode_dies_with_usage():
    result = _run(*_CATALOG, "--domain-type", "kvm")
    assert result.returncode != 0
    assert "--cpu-mode" in result.stderr


def test_invalid_domain_type_dies():
    result = _run(*_CATALOG, "--domain-type", "bogus", "--cpu-mode", "maximum")
    assert result.returncode != 0
    assert "--domain-type must be 'kvm' or 'qemu'" in result.stderr


def test_unknown_arg_dies():
    result = _run(*_CATALOG, *_VIRT, "--bogus")
    assert result.returncode == 2
    assert "unknown arg: --bogus" in result.stderr


def test_dry_run_cache_follows_the_box_name(tmp_path):
    # The cache artifact follows the new box name rhel/<major>-x86_64 -> rhel-<major>-x86_64.box,
    # matching box.base_cache_artifact (#1274).
    result = _run(*_CATALOG, *_VIRT, "--dry-run", cache_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"box cache: {tmp_path}/rhel-9-x86_64.box" in result.stdout
    assert "decision:  BUILD" in result.stdout


def test_dry_run_reuses_a_present_cache(tmp_path):
    (tmp_path / "rhel-9-x86_64.box").write_text("fake")
    result = _run(*_CATALOG, *_VIRT, "--dry-run", cache_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "decision:  REUSE" in result.stdout


_RHEL10 = ["--major", "10", "--point", "10.2", "--iso", "rhel-10.2-x86_64-dvd.iso"]


def test_rhel10_dry_run_names_its_box_cache_and_the_shared_kickstart(tmp_path):
    # T10 (#1286): RHEL 10's Anaconda takes the shared ks.cfg unchanged, so no
    # ks-10.cfg is committed and the shared file is the one chosen.
    assert not SCRIPT.with_name("ks-10.cfg").exists()
    result = _run(*_RHEL10, *_VIRT, "--dry-run", cache_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"box cache: {tmp_path}/rhel-10-x86_64.box" in result.stdout
    assert "kickstart: ks.cfg" in result.stdout


def test_a_per_major_kickstart_is_chosen_when_present(tmp_path):
    """ks-<major>.cfg beside the script wins for that major only (copy of the builder,
    since no per-major kickstart is committed); the other majors keep ks.cfg."""
    boxes = tmp_path / "lab" / "boxes"
    (boxes / "rhel").mkdir(parents=True)
    shutil.copy(SCRIPT, boxes / "rhel" / SCRIPT.name)
    shutil.copy(SCRIPT.parents[1] / "_box-register.sh", boxes / "_box-register.sh")
    (boxes / "rhel" / "ks.cfg").write_text("# shared\n")
    (boxes / "rhel" / "ks-10.cfg").write_text("# major 10\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    copy = boxes / "rhel" / SCRIPT.name
    cache = tmp_path / "cache"
    env = {**os.environ, "LAB_BOX_CACHE_DIR": str(cache)}
    for catalog, want in ((_RHEL10, "ks-10.cfg"), (_CATALOG, "ks.cfg")):
        result = subprocess.run(  # noqa: S603
            ["bash", str(copy), *catalog, *_VIRT, "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert f"kickstart: {want}\n" in result.stdout
