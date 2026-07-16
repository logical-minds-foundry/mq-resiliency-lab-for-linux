from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from mqlab import cli
from mqlab.cli import _prepare_lab as _real_prepare_lab  # captured before the autouse stub
from mqlab.doctor import Check
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.platforms import PlatformError

runner = CliRunner()

VERGIL = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)


# --- mqlab doctor command (exercises the real _doctor_checks) ---
def test_doctor_passes(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(cli, "run_checks", lambda facts, which: [Check("kvm", True, "ok")])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "kvm" in result.stdout


def test_doctor_fails_nonzero(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(
        cli, "run_checks", lambda facts, which: [Check("kvm", False, "no kvm", "enable KVM")]
    )
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "enable KVM" in result.stdout


# --- cold-boot staleness nudge surfaced in doctor (epic .github#91 T6) ---
def test_doctor_surfaces_cold_boot_notice(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(cli, "run_checks", lambda facts, which: [Check("kvm", True, "ok")])
    monkeypatch.setattr(cli.coldboot, "nudge", lambda: "NOTICE: this box is 40 days old.")
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0  # advisory only — never blocks
    assert "NOTICE: this box is 40 days old." in result.stdout
    assert "kvm" in result.stdout  # the normal doctor report still renders


def test_doctor_silent_when_no_cold_boot_nudge(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(cli, "run_checks", lambda facts, which: [Check("kvm", True, "ok")])
    monkeypatch.setattr(cli.coldboot, "nudge", lambda: None)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "NOTICE" not in result.stdout


# --- _prepare_lab (the real function, captured at import) ---
def test_prepare_lab_in_vergil_skips_gate_and_renders(monkeypatch):
    rendered = {}
    monkeypatch.setattr(cli, "probe", lambda: VERGIL)
    monkeypatch.setattr(
        cli, "ensure_resolved", lambda *, facts: rendered.setdefault("facts", facts)
    )
    _real_prepare_lab()
    assert rendered["facts"] is VERGIL


def test_prepare_lab_outside_vergil_passing_checks_renders(monkeypatch):
    rendered = {}
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(cli, "run_checks", lambda facts, which: [Check("kvm", True, "ok")])
    monkeypatch.setattr(cli, "ensure_resolved", lambda *, facts: rendered.setdefault("ok", True))
    _real_prepare_lab()
    assert rendered["ok"] is True


def test_prepare_lab_failing_checks_exits_one(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: X86)
    monkeypatch.setattr(
        cli, "run_checks", lambda facts, which: [Check("kvm", False, "no kvm", "enable KVM")]
    )
    monkeypatch.setattr(cli, "ensure_resolved", lambda *, facts: pytest.fail("must not render"))
    with pytest.raises(typer.Exit) as exc:
        _real_prepare_lab()
    assert exc.value.exit_code == 1


def test_prepare_lab_missing_kvm_exits_one(monkeypatch):
    monkeypatch.setattr(cli, "probe", lambda: VERGIL)  # in-vergil: skip checks, hit ensure_resolved

    def boom(*, facts):
        raise PlatformError("native KVM required")

    monkeypatch.setattr(cli, "ensure_resolved", boom)
    with pytest.raises(typer.Exit) as exc:
        _real_prepare_lab()
    assert exc.value.exit_code == 1
