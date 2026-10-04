from __future__ import annotations

import subprocess

import pytest
import typer
from typer.testing import CliRunner

from mqlab import cli
from mqlab.buildenv import BuildEnvError
from mqlab.cli import _build_ensure as _real_build_ensure  # captured before the autouse stub
from tests.fakes import RecordingRunner, ScriptedResult

runner = CliRunner()


def test_build_path_prints_bucket(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_build_bucket_path", lambda bucket: tmp_path / "build" / bucket)
    result = runner.invoke(cli.app, ["build", "path", "cache"])
    assert result.exit_code == 0
    assert str(tmp_path / "build" / "cache") in result.stdout


def test_build_path_unknown_bucket_exits_two(monkeypatch):
    def boom(bucket):
        raise BuildEnvError("unknown bucket")

    monkeypatch.setattr(cli, "_build_bucket_path", boom)
    assert runner.invoke(cli.app, ["build", "path", "nope"]).exit_code == 2


def test_build_ensure_calls_seam(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_build_ensure", lambda: called.setdefault("y", True))
    assert runner.invoke(cli.app, ["build", "ensure"]).exit_code == 0
    assert called == {"y": True}


def test_build_clean_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: seen.update(kw) or ["work", "temp"])
    result = runner.invoke(cli.app, ["build", "clean"])
    assert result.exit_code == 0
    assert "work" in result.stdout
    assert seen == {"drop_cache": False, "drop_state": False}


def test_build_clean_cache(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: seen.update(kw) or [])
    runner.invoke(cli.app, ["build", "clean", "--cache"])
    assert seen["drop_cache"] is True


def test_build_clean_state_requires_confirmation(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: called.update(kw) or [])
    result = runner.invoke(cli.app, ["build", "clean", "--state"])  # no --yes-destroy-state
    assert result.exit_code == 2
    assert called == {}  # refused before doing anything


def test_build_clean_state_with_confirmation_runs(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_build_clean", lambda **kw: called.update(kw) or ["state"])
    result = runner.invoke(cli.app, ["build", "clean", "--state", "--yes-destroy-state"])
    assert result.exit_code == 0
    assert called["drop_state"] is True


def test_build_status_lists_buckets(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "_build_bucket_path", lambda bucket: tmp_path / "build" / bucket)
    result = runner.invoke(cli.app, ["build", "status"])
    assert result.exit_code == 0
    assert "cache" in result.stdout and "local" in result.stdout


def test_build_status_git_failure_prints_diagnosis_and_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))

    def boom(bucket):
        raise BuildEnvError("git failed (exit 128): git rev-parse --git-dir\n  stderr: fatal: x")

    monkeypatch.setattr(cli, "_build_bucket_path", boom)
    result = runner.invoke(cli.app, ["build", "status"])
    assert result.exit_code == 2
    assert "stderr: fatal: x" in result.stderr


def test_build_migrate_dry_run_and_real(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        cli.buildenv,
        "migrate",
        lambda repo, *, dry_run: cli.buildenv.MigrationPlan(
            moves=[("/a/mq", "/a/cache/mq")],
            duplicates=[("/a/refs/x", "/a/cache/refs/x")],
            stale=["/a/lab/.vagrant is a stale legacy vagrant dotfile"],
        ),
    )
    # migrate also renames the per-host box cache to the arch-suffixed scheme (#103 T4).
    monkeypatch.setattr(
        cli.box, "migrate_box_cache", lambda *, dry_run: [("/b/x.box", "/b/x-x86_64.box")]
    )
    dry = runner.invoke(cli.app, ["build", "migrate", "--dry-run"])
    assert "PLAN /a/mq -> /a/cache/mq" in dry.stdout
    assert "PLAN /b/x.box -> /b/x-x86_64.box" in dry.stdout
    real = runner.invoke(cli.app, ["build", "migrate"])
    assert "MOVED /a/mq -> /a/cache/mq" in real.stdout
    assert "MOVED /b/x.box -> /b/x-x86_64.box" in real.stdout
    assert "PLAN DROP /a/refs/x (byte-identical duplicate of /a/cache/refs/x)" in dry.stdout
    assert "DROPPED /a/refs/x (byte-identical duplicate of /a/cache/refs/x)" in real.stdout
    assert "STALE /a/lab/.vagrant is a stale legacy vagrant dotfile" in real.stdout
    assert real.exit_code == 0


def test_build_migrate_collision_is_a_clean_error_not_a_traceback(monkeypatch, tmp_path):
    # #1295: a migrate collision used to escape as a Python traceback. It must print the
    # diagnosis (naming the fully-qualified fix) and exit non-zero, before any box rename.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))

    def collide(repo, *, dry_run):
        raise BuildEnvError("migrate collision; re-run `mqlab build migrate`.")

    monkeypatch.setattr(cli.buildenv, "migrate", collide)
    boxes = []
    monkeypatch.setattr(cli.box, "migrate_box_cache", lambda *, dry_run: boxes.append(1) or [])
    result = runner.invoke(cli.app, ["build", "migrate"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "mqlab build migrate: migrate collision" in result.stderr
    assert "Traceback" not in result.output
    assert boxes == []


# --- the real seams delegate to buildenv with repo_root() ---
def test_build_bucket_path_seam(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(cli.buildenv, "bucket_path", lambda bucket, repo: repo / "build" / bucket)
    assert cli._build_bucket_path("cache") == tmp_path / "build" / "cache"


def test_build_ensure_seam(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    seen = {}
    monkeypatch.setattr(cli.buildenv, "ensure", lambda repo: seen.setdefault("repo", repo))
    _real_build_ensure()  # the autouse stub neutralises cli._build_ensure; use the real one
    assert seen["repo"] == tmp_path


def test_build_ensure_seam_prints_git_stderr_and_exits_one(monkeypatch, tmp_path, capsys):
    # #1261: the bootstrap transcript must show git's actual reason, not a bare traceback.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    msg = (
        "git failed (exit 128): git rev-parse --git-dir\n  cwd: /repo\n"
        "  stderr: fatal: detected dubious ownership in repository at '/repo'"
    )

    def boom(repo):
        raise BuildEnvError(msg)

    monkeypatch.setattr(cli.buildenv, "ensure", boom)
    with pytest.raises(typer.Exit) as exc:
        _real_build_ensure()
    assert exc.value.exit_code == 1
    assert "mqlab: cannot wire build/: " + msg in capsys.readouterr().err


def test_build_clean_seam(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    seen = {}
    monkeypatch.setattr(
        cli.buildenv,
        "clean",
        lambda repo, *, drop_cache, drop_state: (
            seen.update(dc=drop_cache, ds=drop_state) or ["work"]
        ),
    )
    assert cli._build_clean(drop_cache=True) == ["work"]
    assert seen == {"dc": True, "ds": False}


# --- root callback: wire the buckets before any non-build command runs (#304) ---
def test_root_callback_runs_build_ensure_for_lab_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    # obs open is a side-effect-free surviving command — it just prints URLs, so it
    # exercises the root callback without touching the lab.
    result = runner.invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert calls == ["ensure"]  # the callback wired the buckets before the command body


def test_root_callback_skips_the_build_group(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    monkeypatch.setattr(cli, "_build_bucket_path", lambda bucket: tmp_path / "build" / bucket)
    result = runner.invoke(cli.app, ["build", "path", "cache"])
    assert result.exit_code == 0
    assert calls == []  # build manages buckets explicitly; `build path` stays a cheap lookup


def test_obs_net_state_skips_build_ensure_even_when_git_would_fail(monkeypatch, tmp_path):
    # #1261: render-only `obs net-state` must not depend on git. Wire the REAL
    # _build_ensure against a git runner that always fails: net-state still succeeds.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "networks").mkdir(parents=True)
    (tmp_path / "lab" / "networks" / "net-hb-a.xml").write_text("<network/>")

    def git_fails(args, **kwargs):
        raise subprocess.CalledProcessError(128, args, output="", stderr="fatal: dubious")

    monkeypatch.setattr(cli.buildenv.subprocess, "run", git_fails)
    monkeypatch.setattr(cli, "_build_ensure", _real_build_ensure)
    monkeypatch.setattr(cli, "_virsh_runner", lambda: RecordingRunner(results=[ScriptedResult([])]))
    result = runner.invoke(cli.app, ["obs", "net-state"])
    assert result.exit_code == 0, result.output
    assert 'lab_network_state{network="net-hb-a"} 0' in result.stdout
    assert not (tmp_path / "build").exists()  # wrote nothing under build/


def test_other_obs_commands_still_run_build_ensure(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    result = runner.invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert calls == ["ensure"]  # exactly once: the obs group callback, not the root too


def test_build_writing_command_runs_build_ensure_and_fails_loud_on_git(monkeypatch, tmp_path):
    # A build-writing verb keeps the check: with git failing it stops, printing git's stderr.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))

    def boom(repo):
        raise BuildEnvError("git failed (exit 128): git rev-parse --git-dir\n  stderr: fatal: y")

    monkeypatch.setattr(cli.buildenv, "ensure", boom)
    monkeypatch.setattr(cli, "_build_ensure", _real_build_ensure)
    result = runner.invoke(cli.app, ["obs", "targets"])
    assert result.exit_code == 1
    assert "stderr: fatal: y" in result.stderr


def test_build_free_allowlist_is_exactly_net_state():
    # Guard: additions must be justified in cli._BUILD_FREE's comment (#1261).
    assert frozenset({("obs", "net-state")}) == cli._BUILD_FREE


def test_root_callback_noop_without_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    runner.invoke(cli.app, [])  # bare `mqlab` -> help; no subcommand to wire for
    assert calls == []


def test_main_invokes_the_app(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(True))
    cli.main()
    assert called == [True]
