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

import io
from pathlib import Path

import pytest
from rich.console import Console

from mqlab import cli, libvirtpool
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


def _pool_deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("libvirt-pool", "20260627T000000Z")),
        pauser=_NoPause(),
    )


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
def _subcmds(commands) -> list[str]:
    return [c.argv[c.argv.index("qemu:///system") + 1] for c in commands]


def _pool_list(state_line: str = ""):
    lines = [" Name           State      Autostart", "----------------------------------"]
    if state_line:
        lines.append(state_line)
    return ScriptedResult(lines)


def test_ensure_libvirt_pool_full_lifecycle_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    # 1st call: the pool-list probe (pool ABSENT); then define/build/start/autostart.
    runner = RecordingRunner(results=[_pool_list()] + [ScriptedResult([]) for _ in range(4)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    cli._ensure_libvirt_pool(step=False)
    # the probe ran first, then the full lifecycle against the dedicated pool name
    assert _subcmds(runner.recorded) == [
        "pool-list",
        "pool-define-as",
        "pool-build",
        "pool-start",
        "pool-autostart",
    ]
    # the target dir is the local work bucket's libvirt-images subdir
    define = runner.recorded[1].argv
    target = define[define.index("--target") + 1]
    assert target.endswith("build/work/libvirt-images")


def test_ensure_libvirt_pool_active_only_reasserts_autostart(monkeypatch, tmp_path):
    # Re-run safety: an already-active pool must NOT be re-built/re-started (those can
    # exit non-zero and halt a --from resume) — only autostart is re-asserted.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    runner = RecordingRunner(
        results=[_pool_list(" mqlab-images   active     yes"), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    cli._ensure_libvirt_pool(step=False)
    assert _subcmds(runner.recorded) == ["pool-list", "pool-autostart"]


def test_ensure_libvirt_pool_inactive_skips_define(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    runner = RecordingRunner(
        results=[_pool_list(" mqlab-images   inactive   no")]
        + [ScriptedResult([]) for _ in range(3)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    cli._ensure_libvirt_pool(step=False)
    assert _subcmds(runner.recorded) == ["pool-list", "pool-build", "pool-start", "pool-autostart"]


def test_ensure_libvirt_pool_fails_loud_on_step_failure(monkeypatch, tmp_path):
    # A genuine error (e.g. pool-define-as exits non-zero) is NOT swallowed —
    # it surfaces as a typer.Exit carrying the step's exit code (fail-loud).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: "mqlab-images")
    runner = RecordingRunner(results=[_pool_list(), ScriptedResult(["boom"], exit_code=3)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    with pytest.raises(cli.typer.Exit) as exc:
        cli._ensure_libvirt_pool(step=False)
    assert exc.value.exit_code == 3


def test_ensure_libvirt_pool_noop_on_host_mount(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_libvirt_pool_override", lambda: None)
    monkeypatch.setattr(
        cli, "build_deps", lambda v, t: pytest.fail("local must run no virsh pool ops")
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


# --------------------------------------------------------------------------- #
# CRITICAL — the override actually REACHES the vms-phase `vagrant up`.
#
# The vms-phase step carries env=None, so it inherits this process's environment.
# _bootstrap_run must export _vagrant_env() (VAGRANT_DOTFILE_PATH + the #376 pool
# override) before the phase loop. This snapshots os.environ at the moment the
# `vagrant up` command runs to prove the var is present then — the gap the earlier
# tests (which only asserted _vagrant_env() CONTAINS the var) missed.
# --------------------------------------------------------------------------- #
class _EnvSnapshotRunner:
    """Records commands and, for each, the os.environ value of a watched key."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.recorded: list = []
        self.env_at: list[str | None] = []

    def run(self, command, on_line) -> int:
        import os

        self.recorded.append(command)
        self.env_at.append(os.environ.get(self.key))
        return 0


def test_bootstrap_vms_up_inherits_pool_env(monkeypatch, tmp_path):
    from tests.test_cli_bootstrap import _seed, _states

    _seed(monkeypatch, tmp_path)
    # cloud path: build/ is a real disk -> override active.
    monkeypatch.setattr(cli, "_build_fstype", lambda p: "ext4")
    # only the vms phase is unsatisfied -> the sequencer runs `vagrant up`.
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states(net=True, vms=False))
    # neutralise the vms-phase prereqs (boxes/mq/pool) and secret sourcing — the
    # focus is purely whether `vagrant up` inherits MQLAB_LIBVIRT_POOL.
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"s-{name}")

    runner = _EnvSnapshotRunner("MQLAB_LIBVIRT_POOL")
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    monkeypatch.delenv("MQLAB_LIBVIRT_POOL", raising=False)

    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "vms"])
    assert result.exit_code == 0
    # exactly one command ran (the vagrant up step), and at that moment the pool
    # override was present in os.environ -> the env=None subprocess inherits it.
    vagrant_idx = next(i for i, c in enumerate(runner.recorded) if c.argv[0] == "vagrant")
    assert runner.recorded[vagrant_idx].env is None  # inherits os.environ, not a per-cmd env
    assert runner.env_at[vagrant_idx] == libvirtpool.POOL_NAME
    monkeypatch.delenv("MQLAB_LIBVIRT_POOL", raising=False)


def test_bootstrap_vms_up_no_pool_env_on_host_mount(monkeypatch, tmp_path):
    from tests.test_cli_bootstrap import _seed, _states

    _seed(monkeypatch, tmp_path)
    # local path: build/ is a virtiofs host-mount -> NO override, NO env var.
    monkeypatch.setattr(cli, "_build_fstype", lambda p: "virtiofs")
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states(net=True, vms=False))
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"s-{name}")

    runner = _EnvSnapshotRunner("MQLAB_LIBVIRT_POOL")
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _pool_deps(runner))
    monkeypatch.delenv("MQLAB_LIBVIRT_POOL", raising=False)

    from typer.testing import CliRunner

    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "vms"])
    assert result.exit_code == 0
    vagrant_idx = next(i for i, c in enumerate(runner.recorded) if c.argv[0] == "vagrant")
    assert runner.env_at[vagrant_idx] is None  # local stays on the default pool
