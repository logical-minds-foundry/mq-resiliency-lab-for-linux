"""CLI-side wiring for the env-aware libvirt image pool (#376).

The pure decision/step builders live in mqlab.libvirtpool (tested in
tests/test_libvirtpool.py). Here we cover the cli.py seams that do the host-side
I/O and wire the override into the bring-up:

  * _build_fstype          -> the findmnt probe seam (mocked in these tests)
  * _libvirt_pool_override -> repo-rooted decision via the seam
  * _libvirt_pool_env      -> {} (local) or {MQLAB_LIBVIRT_POOL: name} (cloud)
  * _vagrant_env           -> merges the pool env so every `vagrant up` carries it
  * the vms phase          -> ensures the pool before `vagrant up` (cloud only)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mqlab import cli, libvirtpool
from tests.fakes import RecordingRunner, ScriptedResult


# --------------------------------------------------------------------------- #
# _libvirt_pool_override — the decision, with the fstype probe mocked
# --------------------------------------------------------------------------- #
def test_pool_override_none_on_host_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_build_fstype", lambda p: "virtiofs")
    assert cli._libvirt_pool_override() is None


def test_pool_override_dedicated_on_real_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_build_fstype", lambda p: "ext4")
    assert cli._libvirt_pool_override() == libvirtpool.POOL_NAME


# --------------------------------------------------------------------------- #
# _libvirt_pool_env — the env fragment merged into the vagrant environment
# --------------------------------------------------------------------------- #
def test_pool_env_empty_on_host_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: None)
    assert cli._libvirt_pool_env() == {}


def test_pool_env_sets_var_on_real_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    assert cli._libvirt_pool_env() == {"MQLAB_LIBVIRT_POOL": "mqlab-images"}


# --------------------------------------------------------------------------- #
# _vagrant_env — carries the dotfile AND (cloud only) the pool override
# --------------------------------------------------------------------------- #
def test_vagrant_env_includes_pool_on_real_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    env = cli._vagrant_env()
    assert env["VAGRANT_DOTFILE_PATH"].endswith("build/state/vagrant")
    assert env["MQLAB_LIBVIRT_POOL"] == "mqlab-images"


def test_vagrant_env_omits_pool_on_host_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: None)
    env = cli._vagrant_env()
    assert env["VAGRANT_DOTFILE_PATH"].endswith("build/state/vagrant")
    assert "MQLAB_LIBVIRT_POOL" not in env


# --------------------------------------------------------------------------- #
# _ensure_libvirt_pool — the host-side pool create, before `vagrant up`
# --------------------------------------------------------------------------- #
def test_ensure_libvirt_pool_runs_virsh_on_real_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    captured: list[list[str]] = []

    def fake_execute(verb, steps, *, step_mode):
        captured.extend(s.command.argv for s in steps)

    monkeypatch.setattr(cli, "_execute", fake_execute)
    cli._ensure_libvirt_pool(step=False)
    # define -> build -> start -> autostart, all against the dedicated pool name
    assert captured[0][:2] == ["virsh", "-c"]
    subcmds = [argv[argv.index("qemu:///system") + 1] for argv in captured]
    assert subcmds == ["pool-define-as", "pool-build", "pool-start", "pool-autostart"]
    # the target dir is the local work bucket's libvirt-images subdir
    define = captured[0]
    target = define[define.index("--target") + 1]
    assert target.endswith("build/work/libvirt-images")


def test_ensure_libvirt_pool_noop_on_host_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: None)
    monkeypatch.setattr(
        cli, "_execute", lambda *a, **k: pytest.fail("local must run no virsh pool ops")
    )
    cli._ensure_libvirt_pool(step=False)  # no-op, no failure


# --------------------------------------------------------------------------- #
# vms phase declares + dispatches the libvirt_pool prereq (before `vagrant up`)
# --------------------------------------------------------------------------- #
def _seed_stack(monkeypatch, tmp_path):
    from tests.test_cli_bootstrap import _seed

    _seed(monkeypatch, tmp_path)
    return cli._lookup_stack_or_exit("pcmk-ubuntu")


def test_vms_phase_ensures_libvirt_pool_before_vagrant(monkeypatch, tmp_path):
    stack = _seed_stack(monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: calls.append("boxes"))
    monkeypatch.setattr(cli, "_ensure_mq_artifacts_for_stack", lambda s: calls.append("mq"))
    monkeypatch.setattr(cli, "_ensure_libvirt_pool", lambda *, step: calls.append("libvirt_pool"))
    phase = next(p for p in cli.PHASES if p.name == "vms")
    cli._ensure_prereqs_for_stack(stack, phase, step=False)
    assert "libvirt_pool" in calls


def test_non_vms_phase_does_not_ensure_libvirt_pool(monkeypatch, tmp_path):
    stack = _seed_stack(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "_ensure_libvirt_pool", lambda *, step: pytest.fail("only vms ensures the pool")
    )
    monkeypatch.setattr(cli, "_render_pki_entities", lambda: tmp_path / "pki.json")
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ensure_mq_artifacts_for_stack", lambda s: None)
    phase = next(p for p in cli.PHASES if p.name == "provision")
    cli._ensure_prereqs_for_stack(stack, phase, step=False)  # no pool ensure


# --------------------------------------------------------------------------- #
# _build_fstype — the real findmnt probe seam (argv shape, parse, fail-loud)
# --------------------------------------------------------------------------- #
def test_build_fstype_parses_findmnt_output(monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult(["ext4"])])
    monkeypatch.setattr(cli, "SubprocessRunner", lambda: runner)
    fstype = cli._build_fstype(Path("/repo/build"))
    assert fstype == "ext4"
    argv = runner.recorded[0].argv
    assert argv == ["findmnt", "-no", "FSTYPE", "--target", "/repo/build"]


def test_build_fstype_fails_loud_when_findmnt_errors(monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "SubprocessRunner", lambda: runner)
    with pytest.raises(RuntimeError):
        cli._build_fstype(Path("/repo/build"))
