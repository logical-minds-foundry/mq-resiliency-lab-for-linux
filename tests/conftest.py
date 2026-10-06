from __future__ import annotations

import pytest

from mqlab import cli, instances, perfrun, topology, versions
from tests.boxfleet import REAL_CATALOG, X86_FACTS, x86_fleet
from tests.fakes import FakeSampleSource

# The catalog-derived fleet as an x86_64 host sees it, built once per session.
_X86_FLEET = x86_fleet()


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
def _host_independent_fleet(monkeypatch):
    """Pin box.FLEET to the x86_64 fleet in every test. The live FLEET is derived from
    the catalog for the host running the suite, and Catalog.all_boxes skips RHEL on an
    aarch64 host (epic .github#280), so without this the fleet would differ between the
    arm64 dev VM and x86 CI. Tests of the derivation itself call box._build_fleet with
    injected facts."""
    monkeypatch.setattr(cli.box, "FLEET", dict(_X86_FLEET))


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


@pytest.fixture(autouse=True)
def tmp_state(monkeypatch, tmp_path_factory):
    """Point the per-stack instance records (epic .github#280) at an empty per-test dir,
    so no test reads or writes the developer's real build/state/instances. Returns the
    dir. paths.instances_dir itself is tested directly in tests/test_paths.py."""
    root = tmp_path_factory.mktemp("instances")
    monkeypatch.setattr(instances, "instances_dir", lambda: root)
    return root


@pytest.fixture(autouse=True)
def _stack_not_live(monkeypatch):
    """Neutralise cli._stack_live in every test — it shells `virsh list --all` to decide
    whether a stack has live domains (the instance-record gate, epic .github#280). Every
    stack reads as not live, so the record gate passes; tests of the gate override this,
    and the real probe is tested via the import captured before this stub."""
    monkeypatch.setattr(cli, "_stack_live", lambda stack_name: False)


@pytest.fixture(autouse=True)
def _x86_host_facts(monkeypatch):
    """Neutralise cli._host_facts in every test — it probes the live host (arch, KVM,
    os-release). The version resolver at bootstrap sees an x86_64 host, so RHEL stacks
    resolve the same on the arm64 dev VM and x86 CI; aarch64 tests override this."""
    monkeypatch.setattr(cli, "_host_facts", lambda: X86_FACTS)


@pytest.fixture(autouse=True)
def _committed_versions_catalog(monkeypatch):
    """Resolve the version layer against the COMMITTED lab/versions.yaml in every test
    (epic .github#280), even when a test re-roots the repo (MQLAB_REPO_ROOT) to seed its
    own topology: a seeded topology fakes the lab shape, never the OS catalog. A test
    exercising a different catalog passes its path to load_catalog explicitly.

    The catalog's ``roles.<role>.components`` names are checked against the COMMITTED
    components/ for the same reason (epic .github#294): a re-rooted test fakes the lab
    shape, never the component set. A test exercising other components patches
    ``versions.components_dir`` itself."""
    monkeypatch.setattr(versions, "versions_catalog_path", lambda: REAL_CATALOG)
    monkeypatch.setattr(versions, "components_dir", lambda: REAL_CATALOG.parents[1] / "components")
