from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from mqlab import cli


def _patch_phases(monkeypatch) -> list[tuple[str, tuple, dict]]:
    """Replace the bring-up phases with recorders; return the call log."""
    calls: list[tuple[str, tuple, dict]] = []
    monkeypatch.setattr(cli, "_prepare_lab", lambda: calls.append(("prepare", (), {})))
    monkeypatch.setattr(cli, "net_create", lambda *a, **k: calls.append(("net", a, k)))
    monkeypatch.setattr(cli, "vm_create", lambda *a, **k: calls.append(("vm", a, k)))
    monkeypatch.setattr(cli, "obs_up", lambda *a, **k: calls.append(("obs", a, k)))
    monkeypatch.setattr(cli, "_lookup_setup_or_exit", lambda name: name)
    return calls


def test_bootstrap_runs_phases_in_order(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    cli._bootstrap_run("distributed-pcmk-ubuntu", manifest=None, step=False)

    assert [c[0] for c in calls] == ["prepare", "net", "vm", "obs"]
    # vm phase gets the setup name + manifest threaded through
    vm_call = next(c for c in calls if c[0] == "vm")
    assert vm_call[1][0] == "distributed-pcmk-ubuntu"
    assert vm_call[2]["manifest"] is None


def test_bootstrap_threads_manifest_and_step(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    cli._bootstrap_run("distributed-pcmk-ubuntu", manifest="2026-06-01", step=True)

    net_call = next(c for c in calls if c[0] == "net")
    vm_call = next(c for c in calls if c[0] == "vm")
    obs_call = next(c for c in calls if c[0] == "obs")
    assert net_call[2]["step"] is True
    assert vm_call[2] == {"manifest": "2026-06-01", "step": True}
    assert obs_call[2]["step"] is True


def test_bootstrap_halts_on_phase_failure(monkeypatch) -> None:
    calls = _patch_phases(monkeypatch)

    def boom(*a, **k):
        raise typer.Exit(code=1)

    monkeypatch.setattr(cli, "vm_create", boom)

    with pytest.raises(typer.Exit) as exc:
        cli._bootstrap_run("distributed-pcmk-ubuntu", manifest=None, step=False)

    assert exc.value.exit_code == 1
    # obs phase must NOT run after vm fails
    assert [c[0] for c in calls] == ["prepare", "net"]


def test_bootstrap_unknown_setup_exits_2(monkeypatch) -> None:
    def reject(name):
        raise typer.Exit(code=2)

    monkeypatch.setattr(cli, "_lookup_setup_or_exit", reject)

    with pytest.raises(typer.Exit) as exc:
        cli._bootstrap_run("nope", manifest=None, step=False)

    assert exc.value.exit_code == 2


def test_bootstrap_command_is_wired(monkeypatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_bootstrap_run",
        lambda setup_name, *, manifest, step: seen.update(
            setup_name=setup_name, manifest=manifest, step=step
        ),
    )

    result = CliRunner().invoke(cli.app, ["bootstrap", "distributed-pcmk-ubuntu"])

    assert result.exit_code == 0
    assert seen == {"setup_name": "distributed-pcmk-ubuntu", "manifest": None, "step": False}
