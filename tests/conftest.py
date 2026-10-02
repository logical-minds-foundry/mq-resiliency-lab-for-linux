from __future__ import annotations

import pytest

from mqlab import cli, perfrun, topology
from tests.fakes import FakeSampleSource


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
def _clear_mqlab_env(monkeypatch):
    """Keep the suite hermetic against the developer's shell: an exported MQLAB_ENV
    (#1202) would apply a per-environment profile to every topology load. Tests that
    exercise a profile set it explicitly."""
    monkeypatch.delenv("MQLAB_ENV", raising=False)


@pytest.fixture(autouse=True)
def _unknown_platform(monkeypatch, tmp_path_factory):
    """Make platform auto-detection (#1245) deterministically INCONCLUSIVE in every
    test, so the suite never picks up the host's real platform (the macOS dev VM would
    otherwise detect `macos` and apply its profile to every topology load). DMI points
    at a fake dir naming an unknown product, and systemd-detect-virt is stubbed out.
    Tests that exercise detection set their own DMI dir / stub; the real
    run_detect_virt is tested via the import captured before this stub."""
    dmi = tmp_path_factory.mktemp("dmi-unknown")  # not tmp_path: tests list their own
    (dmi / "product_name").write_text("Unknown Test Platform\n")
    (dmi / "sys_vendor").write_text("Test Vendor\n")
    monkeypatch.setattr(topology, "DMI_ROOT", dmi)
    monkeypatch.setattr(
        topology, "run_detect_virt", lambda: (None, "systemd-detect-virt stubbed in tests")
    )


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
def _neutralize_reconcile_box_meta(monkeypatch):
    """Neutralise cli._reconcile_box_meta in every test — it reads the rendered
    resolved topology (via _resolved_nodes) to detect a box repoint before the vms
    phase's `vagrant up` (#858), which unit tests do not render. The real function
    and its pure planner are tested directly via the import captured before this
    stub (mirrors the _neutralize_ensure_local_boxes pattern)."""
    monkeypatch.setattr(cli, "_reconcile_box_meta", lambda guests, *, step: None)


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


@pytest.fixture(autouse=True)
def _empty_vagrant_home(monkeypatch, tmp_path_factory):
    """Point VAGRANT_HOME at an empty dir in every test, so the box GC's "which base
    volume is current" read (#1248, box._current_box_volumes) never sees the developer's
    real registered boxes. Tests that need registered boxes populate their own dir."""
    monkeypatch.setenv("VAGRANT_HOME", str(tmp_path_factory.mktemp("vagrant-home")))


@pytest.fixture(autouse=True)
def fake_perf_source(monkeypatch):
    """Replace the bootstrap perf sampler's lab source (#1205) in every test — the real
    one shells `virsh` + ssh into guests, which must not run in unit tests. Returns the
    fake so a test can assert which guests were probed."""
    source = FakeSampleSource()
    monkeypatch.setattr(perfrun, "default_source", lambda topo: source)
    return source
