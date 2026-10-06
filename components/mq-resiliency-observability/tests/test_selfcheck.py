from __future__ import annotations

import importlib.metadata

from mqro import selfcheck

COLLECTORS = ("clusterstate", "nativehastate", "rdqmstate", "loglifecycle")


def test_selfcheck_imports_every_module(capsys):
    assert selfcheck.main([]) == 0
    out = capsys.readouterr().out
    for mod in COLLECTORS:
        assert f"ok mqro.{mod}" in out
    assert "ok mqro.selfcheck" in out


def test_selfcheck_reports_the_distribution_version(capsys):
    assert selfcheck.main() == 0
    version = importlib.metadata.version("mq-resiliency-observability")
    assert f"mq-resiliency-observability {version} on CPython 3.14." in capsys.readouterr().out


def test_selfcheck_fails_on_wrong_minor(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck.sys, "version_info", (3, 12, 3, "final", 0))
    assert selfcheck.main([]) == 1
    assert "is not CPython 3.14" in capsys.readouterr().err


def test_selfcheck_fails_on_wrong_implementation(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck.platform, "python_implementation", lambda: "PyPy")
    assert selfcheck.main([]) == 1
    assert "PyPy" in capsys.readouterr().err


def test_selfcheck_fails_loud_on_import_error(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck, "_modules", lambda: ["mqro.clusterstate", "mqro.nope"])
    assert selfcheck.main([]) == 1
    captured = capsys.readouterr()
    assert "FAIL mqro.nope: ModuleNotFoundError" in captured.err
    assert "ok mqro.clusterstate" in captured.out
