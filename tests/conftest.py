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
def _neutralize_stack_host_arch_gate(monkeypatch):
    """Neutralise cli._gate_stack_host_arch in every test — it calls probe() (real
    host I/O) to abort a RHEL stack on aarch64 before any box bake (#847). Unit
    tests must not read the live host; the gate is tested directly via the import
    captured before this stub (mirrors the _prepare_lab pattern)."""
    monkeypatch.setattr(cli, "_gate_stack_host_arch", lambda stack: None)


@pytest.fixture(autouse=True)
def _neutralize_ensure_local_boxes(monkeypatch):
    """Neutralise cli._ensure_local_boxes in every test — it reads the rendered
    resolved topology and shells `vagrant box list` / build-box.sh, which must not
    run in unit tests (#276). The real function is tested directly via the import
    captured before this stub."""
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda guests: None)


@pytest.fixture(autouse=True)
def _neutralize_build_ensure(monkeypatch):
    """Neutralise cli._build_ensure in every test — it shells git + makes symlinks
    (#286), which must not run in unit tests. The build commands test it via their own
    seams; _prepare_lab's call is covered with this stub in place."""
    monkeypatch.setattr(cli, "_build_ensure", lambda: None)


@pytest.fixture(autouse=True)
def _neutralize_venv_sync(monkeypatch):
    """Neutralise venvsync.ensure_venv_current in every test — the lab-lifecycle
    verbs (bootstrap/teardown/box build) call it up front and it shells `uv sync`
    (#776), which must not run in unit tests. Patching the module attribute covers
    every call site (cli + box reference the same module object). The helper's own
    behaviour is tested directly in tests/test_venvsync.py."""
    monkeypatch.setattr(cli.venvsync, "ensure_venv_current", lambda: None)


@pytest.fixture(autouse=True)
def _neutralize_box_gc(monkeypatch):
    """Neutralise the box base-image GC hook in every test — after a bake or a
    teardown it shells `virsh vol-list`/`vol-delete` against the libvirt pool (#759),
    which must not run in unit tests. Dedicated tests override this stub to cover the
    GC logic (box.gc_orphaned_images) and the hooks firing."""
    monkeypatch.setattr(cli.box, "gc_orphaned_images_best_effort", lambda: None)
