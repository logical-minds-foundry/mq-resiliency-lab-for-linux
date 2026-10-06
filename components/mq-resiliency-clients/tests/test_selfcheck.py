"""The component selfcheck (epic .github#294 spec §5.4 rule 4)."""

from __future__ import annotations

import importlib.metadata
import sys
import types

from mqrc import selfcheck

MODULES = (
    "app_requester",
    "bench_client",
    "svc_responder",
    "authz_probe",
    "dlq_probe",
    "reconnect_probe",
    "dr_flow",
    "dr_responder",
    "dr_mqi",
    "dr_baseline",
    "dr_forced",
    "header",
    "dr",
    "dr.ledger",
    "selfcheck",
)

_REAL_VERSION = importlib.metadata.version


def _pymqi_dist(installed: bool):
    """A stand-in for importlib.metadata.version that reports (or denies) a pymqi
    distribution and defers to the real lookup for everything else."""

    def version(name: str) -> str:
        if name == "pymqi":
            if installed:
                return "1.12.13"
            raise importlib.metadata.PackageNotFoundError(name)
        return _REAL_VERSION(name)

    return version


def test_selfcheck_imports_every_module(capsys):
    assert selfcheck.main([]) == 0
    out = capsys.readouterr().out
    for mod in MODULES:
        assert f"ok mqrc.{mod}\n" in out
    assert "mq-resiliency-clients 0.1.0 on 3.14" in out


def test_selfcheck_fails_on_wrong_minor(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck.sys, "version_info", (3, 12, 3, "final", 0))
    assert selfcheck.main([]) == 1
    assert "is not CPython 3.14" in capsys.readouterr().err


def test_selfcheck_fails_loud_on_import_error(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck, "_modules", lambda: ["mqrc.nope"])
    assert selfcheck.main([]) == 1
    assert "mqrc.nope" in capsys.readouterr().err


def test_selfcheck_skips_pymqi_without_the_mqi_extra(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _pymqi_dist(installed=False))
    assert selfcheck.main([]) == 0
    assert "real-binding check skipped" in capsys.readouterr().out


def test_selfcheck_requires_pymqi_when_installed_with_mqi(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _pymqi_dist(installed=True))
    monkeypatch.setitem(sys.modules, "pymqi", None)  # distribution present, binding broken
    assert selfcheck.main([]) == 1
    assert "pymqi" in capsys.readouterr().err


def test_selfcheck_imports_the_real_pymqi_when_installed(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _pymqi_dist(installed=True))
    monkeypatch.setitem(sys.modules, "pymqi", types.ModuleType("pymqi"))
    assert selfcheck.main([]) == 0
    # the version is the distribution's, never pymqi.__version__ (stale upstream)
    assert "ok pymqi 1.12.13" in capsys.readouterr().out
