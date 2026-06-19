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
