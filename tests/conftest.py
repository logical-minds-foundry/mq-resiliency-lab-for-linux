from __future__ import annotations

import pytest

from mqlab import cli


@pytest.fixture(autouse=True)
def prepare_lab_calls(monkeypatch):
    """Neutralise cli._prepare_lab in every test.

    _prepare_lab does real host I/O (probe) and enforces the native-KVM requirement
    + renders build/lab/topology.resolved.yaml (#276). That must not run in unit tests
    or on a no-KVM CI runner. The fixture records calls so a test can assert whether a
    verb gated (positive) or not (negative).
    """
    calls: list[str] = []
    monkeypatch.setattr(cli, "_prepare_lab", lambda: calls.append("prepare"))
    return calls


@pytest.fixture(autouse=True)
def _neutralize_ensure_local_boxes(monkeypatch):
    """Neutralise cli._ensure_local_boxes in every test — it reads the rendered
    resolved topology and shells `vagrant box list` / build-box.sh, which must not
    run in unit tests (#276). The real function is tested directly via the import
    captured before this stub."""
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda guests: None)


@pytest.fixture(autouse=True)
def _neutralize_sweep_orphan_volumes(monkeypatch):
    """Neutralise cli._sweep_orphan_volumes in every test — it shells `virsh vol-list`
    / `vol-delete`, which must not run in unit tests (#276). Tested directly via the
    import captured before this stub."""
    monkeypatch.setattr(cli, "_sweep_orphan_volumes", lambda guests: None)


@pytest.fixture(autouse=True)
def _neutralize_build_ensure(monkeypatch):
    """Neutralise cli._build_ensure in every test — it shells git + makes symlinks
    (#286), which must not run in unit tests. The build commands test it via their own
    seams; _prepare_lab's call is covered with this stub in place."""
    monkeypatch.setattr(cli, "_build_ensure", lambda: None)
