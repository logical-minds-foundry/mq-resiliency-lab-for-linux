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
    "ibmmq_refs",
    "dr",
    "dr.ledger",
    "selfcheck",
)

_REAL_VERSION = importlib.metadata.version


def _ibmmq_dist(installed: bool):
    """A stand-in for importlib.metadata.version that reports (or denies) an ibmmq
    distribution and defers to the real lookup for everything else."""

    def version(name: str) -> str:
        if name == "ibmmq":
            if installed:
                return "2.1.1"
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


def test_selfcheck_skips_ibmmq_without_the_mqi_extra(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=False))
    assert selfcheck.main([]) == 0
    assert (
        "ibmmq not installed (no mqi extra): real-binding check skipped" in capsys.readouterr().out
    )


def test_selfcheck_requires_ibmmq_when_installed_with_mqi(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=True))
    monkeypatch.setitem(sys.modules, "ibmmq", None)  # distribution present, binding broken
    assert selfcheck.main([]) == 1
    assert "FAIL ibmmq" in capsys.readouterr().err


def test_selfcheck_fails_on_a_binding_version_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=True))
    monkeypatch.delitem(sys.modules, "ibmmq", raising=False)
    real_import = importlib.import_module

    def import_module(name, *a, **kw):
        if name == "ibmmq":
            raise OSError("Mismatch between C '2.1.0' and Python '2.1.1' package versions")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", import_module)
    assert selfcheck.main([]) == 1
    assert "FAIL ibmmq: OSError" in capsys.readouterr().err


def test_selfcheck_imports_the_real_ibmmq_when_installed(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=True))
    binding = types.ModuleType("ibmmq")
    monkeypatch.setitem(sys.modules, "ibmmq", binding)
    seen = []

    def problems(b, refs, members):
        seen.append((b, refs, members))
        return []

    monkeypatch.setattr(selfcheck.ibmmq_refs, "problems", problems)
    assert selfcheck.main([]) == 0
    out = capsys.readouterr().out
    assert "ok ibmmq 2.1.1" in out
    # the API check ran against the imported binding, over the package's own source
    ((b, refs, members),) = seen
    assert b is binding
    assert f"ok ibmmq API: {len(refs)} references, {len(members)} members" in out
    assert any(r.path == ("CMQXC", "MQXPT_TCP") for r in refs)


def test_selfcheck_fails_when_the_binding_lacks_a_used_name(monkeypatch, capsys):
    # #1478: a binding without a name the clients use fails the install, per reference.
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=True))
    monkeypatch.setitem(sys.modules, "ibmmq", types.ModuleType("ibmmq"))  # has nothing
    assert selfcheck.main([]) == 1
    err = capsys.readouterr().err
    assert "FAIL ibmmq API: app_requester.py:" in err
    assert ": ibmmq.CMQXC does not exist" in err


def test_selfcheck_fails_when_the_api_check_cannot_run(monkeypatch, capsys):
    monkeypatch.setattr(importlib.metadata, "version", _ibmmq_dist(installed=True))
    monkeypatch.setitem(sys.modules, "ibmmq", types.ModuleType("ibmmq"))

    def problems(*_a):
        raise RuntimeError("structure would not build")

    monkeypatch.setattr(selfcheck.ibmmq_refs, "problems", problems)
    assert selfcheck.main([]) == 1
    assert "FAIL ibmmq API check: RuntimeError" in capsys.readouterr().err
