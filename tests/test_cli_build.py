from __future__ import annotations

from typer.testing import CliRunner

from mqlab import cli
from mqlab.buildenv import BuildEnvError
from mqlab.cli import _build_ensure as _real_build_ensure  # captured before the autouse stub

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


def test_build_migrate_dry_run_and_real(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        cli.buildenv, "migrate", lambda repo, *, dry_run: [("/a/mq", "/a/cache/mq")]
    )
    dry = runner.invoke(cli.app, ["build", "migrate", "--dry-run"])
    assert "PLAN /a/mq -> /a/cache/mq" in dry.stdout
    real = runner.invoke(cli.app, ["build", "migrate"])
    assert "MOVED /a/mq -> /a/cache/mq" in real.stdout


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
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: None)  # neutralise the command body
    result = runner.invoke(cli.app, ["net", "status"])
    assert result.exit_code == 0
    assert calls == ["ensure"]  # the callback wired the buckets before the command's transcript


def test_root_callback_skips_the_build_group(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    monkeypatch.setattr(cli, "_build_bucket_path", lambda bucket: tmp_path / "build" / bucket)
    result = runner.invoke(cli.app, ["build", "path", "cache"])
    assert result.exit_code == 0
    assert calls == []  # build manages buckets explicitly; `build path` stays a cheap lookup


def test_root_callback_noop_without_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_build_ensure", lambda: calls.append("ensure"))
    runner.invoke(cli.app, [])  # bare `mqlab` -> help; no subcommand to wire for
    assert calls == []
