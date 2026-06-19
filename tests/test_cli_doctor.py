from __future__ import annotations

from typer.testing import CliRunner

from mqlab import cli
from mqlab.doctor import Check

runner = CliRunner()


def test_doctor_passes(monkeypatch):
    monkeypatch.setattr(cli, "_doctor_checks", lambda: [Check("kvm", True, "ok")])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "kvm" in result.stdout


def test_doctor_fails_nonzero(monkeypatch):
    monkeypatch.setattr(
        cli, "_doctor_checks", lambda: [Check("kvm", False, "no kvm", "enable KVM")]
    )
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "enable KVM" in result.stdout
