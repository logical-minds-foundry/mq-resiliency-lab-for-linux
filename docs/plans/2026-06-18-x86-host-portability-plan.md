# x86 Host Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the lab's virtualization/emulation decisions a function of the real host architecture so the repo runs natively (KVM) on x86 Linux while the arm64 Mac dev path keeps working unchanged.

**Architecture:** A single Python authority (`hostfacts` + `platforms`) probes the host once and resolves each topology node to a concrete libvirt provider config, rendered to `build/lab/topology.resolved.yaml` for a now-"dumb" Vagrantfile. Native-preferred guest arch is a host-dependent *default platform* (arch-explicit strings), so the version-manifest subsystem (#266) acquires the right MQ tarball with no parallel fetcher. A `mqlab doctor` preflight enforces the hard native-KVM requirement and diagnoses missing host prerequisites.

**Tech Stack:** Python 3.12 (typer CLI, frozen dataclasses, PyYAML), Ruby (Vagrantfile), Ansible, Bash. Tests: pytest with dependency injection.

## Global Constraints

- **Python 3.12.** No syntax/stdlib above 3.12.
- **Validation is one command only:** `vrg-container-run -- vrg-validate`. Never run linters/pytest directly as the gate.
- **100% branch coverage is enforced.** All new code must be reachable by injected fakes — every function that touches the real host (`platform.machine()`, `/dev/kvm`, `/etc/os-release`, `/etc/vergil`, `shutil.which`, subprocess) takes its inputs as injectable parameters/callables. Mark only genuinely unreachable real-IO lines `# pragma: no cover`.
- **Fail loud. No silent fallbacks** (no `except: pass`, no default that hides an error). New errors are `RuntimeError` subclasses, mirroring `InventoryError` / `StepFailedError`.
- **Native-arch KVM is a hard requirement** (D3): missing `/dev/kvm` for the host arch → hard stop, no opt-in.
- **Platform strings stay arch-explicit** (D6): `ubuntu2404-arm64`, `ubuntu2404-x86_64`, `rhel96-x86_64`. Native-preferred is a host-dependent *default*, never a logical sentinel.
- **Integrate with #266, do not duplicate it** (D6): reuse `manifest`/`artifact`; the Vagrantfile keeps reading `build/box-versions.json`.
- **Confirmed facts** (do not re-derive, but a verification step is fine): in-Vergil marker = `/etc/vergil` exists; x86_64 Ubuntu MQ tarball = `…-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz`; `cloud-image/ubuntu-24.04` ships both `libvirt` arch variants (box name unchanged).
- **Git:** all work in this worktree (`.worktrees/issue-276-x86-host-portability`); commit with `vrg-commit --type <t> --scope <s> --message <m>` (never raw git commit). Branch: `feature/276-x86-host-portability`.
- **Ruff:** respect the magic trailing comma (a trailing comma forces multi-line). Avoid `StrEnum` (UP042 friction) — use module-level `str` constants as the codebase already does.

## File Structure

| Path | New? | Responsibility |
|------|------|----------------|
| `src/mqlab/hostfacts.py` | new | Probe + normalise the host: arch, kvm, distro family, in-Vergil. The only module that touches host I/O. |
| `src/mqlab/platforms.py` | new | Pure resolver: `default_platform(facts)`, `resolve(topo, facts) → {name: ResolvedNode}`, `require_native_kvm(facts)`; resolved-file render + `ensure_resolved()`. |
| `src/mqlab/doctor.py` | new | Preflight checklist (pure given injected probes) + suggestions. |
| `src/mqlab/paths.py` | modify | Add `resolved_topology_path()`. |
| `src/mqlab/fleet.py` | modify | `lab_guests(facts=None)` host-aware default via `default_platform`. |
| `src/mqlab/manifest.py` | modify | `_ARCH_SUFFIX` gains `ubuntu2404-x86_64`; `setup_platforms(setup, facts=None)` threads facts. |
| `src/mqlab/cli.py` | modify | `mqlab doctor` command; call `ensure_resolved()` (+ doctor hard-gate) before every `vagrant`-shelling verb. |
| `lab/topology.yaml` | modify | Add `ubuntu2404-x86_64` platform; drop vestigial `defaults.platform`. |
| `lab/Vagrantfile` | modify | Consume `build/lab/topology.resolved.yaml`; delete `case arch`; keep `box-versions.json`. |
| `ansible/roles/mq-install/tasks/main.yml` | modify | Ubuntu tar suffix from `ansible_architecture`. |
| `ansible/roles/mq-client/tasks/main.yml` | modify | Ubuntu tar suffix from `ansible_architecture`. |
| `scripts/fetch-mq.sh` | modify | Fetch host-arch Ubuntu tar + `LinuxX64`. |
| `docs/development/lab-bringup-capture.md` | modify | Document the host-arch artifact behaviour. |
| `tests/test_hostfacts.py` | new | hostfacts normalisation + probe injection. |
| `tests/test_platforms.py` | new | resolution matrix, default_platform, guards, render. |
| `tests/test_doctor.py` | new | checklist verdicts + suggestions. |
| `tests/test_fleet.py` | modify | inject facts. |
| `tests/test_manifest.py` | modify | inject facts; new suffix. |
| `tests/test_cli_doctor.py` | new | `mqlab doctor` CLI. |

---

### Task 1: `hostfacts` — probe and normalise the host

**Files:**
- Create: `src/mqlab/hostfacts.py`
- Test: `tests/test_hostfacts.py`

**Interfaces:**
- Produces: `AARCH64="aarch64"`, `X86_64="x86_64"`; `class HostFactError(RuntimeError)`; `@dataclass(frozen=True) HostFacts(arch:str, kvm:bool, distro_family:str, in_vergil:bool)`; `normalize_arch(machine:str)->str`; `distro_family(os_release_text:str)->str`; `from_raw(*, machine:str, kvm_usable:bool, os_release_text:str, vergil_marker:bool)->HostFacts`; `probe(*, machine=..., kvm_path=Path("/dev/kvm"), os_release=Path("/etc/os-release"), vergil_marker=Path("/etc/vergil"))->HostFacts`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hostfacts.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mqlab import hostfacts as hf


@pytest.mark.parametrize(
    ("raw", "want"),
    [("aarch64", hf.AARCH64), ("arm64", hf.AARCH64), ("x86_64", hf.X86_64), ("amd64", hf.X86_64)],
)
def test_normalize_arch_folds_aliases(raw, want):
    assert hf.normalize_arch(raw) == want


def test_normalize_arch_rejects_unknown():
    with pytest.raises(hf.HostFactError):
        hf.normalize_arch("riscv64")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ('ID=ubuntu\nID_LIKE=debian\n', "apt"),
        ('ID="rhel"\nID_LIKE="fedora"\n', "dnf"),
        ('ID=almalinux\nID_LIKE="rhel centos fedora"\n', "dnf"),
        ('ID=arch\n', "unknown"),
        ("", "unknown"),
    ],
)
def test_distro_family(text, want):
    assert hf.distro_family(text) == want


def test_from_raw_builds_facts():
    f = hf.from_raw(machine="amd64", kvm_usable=True, os_release_text="ID=ubuntu\n", vergil_marker=False)
    assert f == hf.HostFacts(arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=False)


def test_probe_reads_present_files(tmp_path):
    osr = tmp_path / "os-release"
    osr.write_text("ID=ubuntu\n")
    kvm = tmp_path / "kvm"
    kvm.write_text("")  # exists + writable in tmp
    vrg = tmp_path / "vergil"
    vrg.write_text("")
    f = hf.probe(machine=lambda: "x86_64", kvm_path=kvm, os_release=osr, vergil_marker=vrg)
    assert f == hf.HostFacts(arch=hf.X86_64, kvm=True, distro_family="apt", in_vergil=True)


def test_probe_handles_absent_files(tmp_path):
    missing = tmp_path / "nope"
    f = hf.probe(machine=lambda: "aarch64", kvm_path=missing, os_release=missing, vergil_marker=missing)
    assert f == hf.HostFacts(arch=hf.AARCH64, kvm=False, distro_family="unknown", in_vergil=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .worktrees/issue-276-x86-host-portability && uv run pytest tests/test_hostfacts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.hostfacts'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/hostfacts.py
"""Probe and normalise the host — the single I/O boundary for arch-gated decisions (#276).

Every consumer takes a HostFacts value, so the host-arch matrix is testable with no
real hardware. probe() is the only function that reads the live host.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from platform import machine as _machine

AARCH64 = "aarch64"
X86_64 = "x86_64"

_ARCH_ALIASES = {"aarch64": AARCH64, "arm64": AARCH64, "x86_64": X86_64, "amd64": X86_64}
_APT = {"ubuntu", "debian"}
_DNF = {"rhel", "fedora", "centos", "almalinux", "rocky"}


class HostFactError(RuntimeError):
    """The host cannot be characterised (e.g. an unknown CPU architecture)."""


@dataclass(frozen=True)
class HostFacts:
    arch: str
    kvm: bool
    distro_family: str
    in_vergil: bool


def normalize_arch(machine: str) -> str:
    key = machine.strip().lower()
    if key not in _ARCH_ALIASES:
        raise HostFactError(f"unsupported host architecture: {machine!r}")
    return _ARCH_ALIASES[key]


def distro_family(os_release_text: str) -> str:
    ids: dict[str, str] = {}
    for line in os_release_text.splitlines():
        key, sep, val = line.partition("=")
        if sep:
            ids[key.strip()] = val.strip().strip('"').lower()
    tokens = {ids.get("ID", "")} | set(ids.get("ID_LIKE", "").split())
    if _APT & tokens:
        return "apt"
    if _DNF & tokens:
        return "dnf"
    return "unknown"


def from_raw(*, machine: str, kvm_usable: bool, os_release_text: str, vergil_marker: bool) -> HostFacts:
    return HostFacts(
        arch=normalize_arch(machine),
        kvm=kvm_usable,
        distro_family=distro_family(os_release_text),
        in_vergil=vergil_marker,
    )


def probe(
    *,
    machine: Callable[[], str] = _machine,
    kvm_path: Path = Path("/dev/kvm"),
    os_release: Path = Path("/etc/os-release"),
    vergil_marker: Path = Path("/etc/vergil"),
) -> HostFacts:
    return from_raw(
        machine=machine(),
        kvm_usable=os.access(kvm_path, os.R_OK | os.W_OK),
        os_release_text=os_release.read_text() if os_release.exists() else "",
        vergil_marker=vergil_marker.exists(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .worktrees/issue-276-x86-host-portability && uv run pytest tests/test_hostfacts.py -q`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
cd .worktrees/issue-276-x86-host-portability
vrg-git add src/mqlab/hostfacts.py tests/test_hostfacts.py
vrg-commit --type feat --scope hostfacts --message "probe + normalise host arch/kvm/distro/in-vergil (#276)"
```

---

### Task 2: `platforms` — the pure resolution matrix

**Files:**
- Create: `src/mqlab/platforms.py`
- Test: `tests/test_platforms.py`

**Interfaces:**
- Consumes: `mqlab.hostfacts.{HostFacts, AARCH64, X86_64}`.
- Produces: `class PlatformError(RuntimeError)`; `@dataclass(frozen=True) ResolvedNode(platform, box, arch, driver, machine_arch, machine_type, loader, nvram, input_bus, cpu_mode, boot_timeout, cpus, memory, extra_disk, dvd, nics)`; `default_platform(facts)->str`; `require_native_kvm(facts)->None`; `resolve(topo:dict, facts)->dict[str, ResolvedNode]`.
- Note: `resolve` is display-safe — it never raises on missing KVM (so `vm status` works on a broken host); the hard KVM gate lives in `require_native_kvm`, called by `ensure_resolved`/`doctor`. `resolve` *does* raise on the structurally-impossible arm64-on-x86 (D4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platforms.py
from __future__ import annotations

import pytest

from mqlab import platforms as p
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

ARM_KVM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86_KVM = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
X86_NOKVM = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=False)

TOPO = {
    "boxes": {
        "ubuntu2404-arm64": {"box": "cloud-image/ubuntu-24.04", "arch": "aarch64"},
        "ubuntu2404-x86_64": {"box": "cloud-image/ubuntu-24.04", "arch": "x86_64"},
        "rhel96-x86_64": {"box": "rhel/9.6-x86_64", "arch": "x86_64", "dvd": "/iso/rhel.iso"},
    },
    "defaults": {"cpus": 1, "memory": 1024},
    "nodes": {
        "obs": {"cpus": 2, "memory": 4096, "nics": {"net-mgmt": "10.50.0.2"}},
        "rdqm-a1": {"platform": "rhel96-x86_64", "extra_disk": 10, "nics": {"net-mgmt": "10.50.0.31"}},
    },
}


def test_default_platform_tracks_host():
    assert p.default_platform(ARM_KVM) == "ubuntu2404-arm64"
    assert p.default_platform(X86_KVM) == "ubuntu2404-x86_64"


def test_resolve_arm_host_ubuntu_is_native_kvm():
    obs = p.resolve(TOPO, ARM_KVM)["obs"]
    assert (obs.platform, obs.arch, obs.driver, obs.cpu_mode) == ("ubuntu2404-arm64", AARCH64, "kvm", "host-passthrough")
    assert obs.loader and obs.nvram and obs.input_bus == "virtio"
    assert obs.boot_timeout is None and obs.machine_arch is None


def test_resolve_arm_host_rhel_is_foreign_tcg():
    n = p.resolve(TOPO, ARM_KVM)["rdqm-a1"]
    assert (n.arch, n.driver, n.cpu_mode, n.boot_timeout) == (X86_64, "qemu", "maximum", 1800)
    assert n.machine_arch == X86_64 and n.machine_type == "q35" and n.loader is None
    assert n.dvd == "/iso/rhel.iso" and n.extra_disk == 10


def test_resolve_x86_host_everything_native_kvm():
    res = p.resolve(TOPO, X86_KVM)
    assert res["obs"].platform == "ubuntu2404-x86_64"
    for n in res.values():
        assert n.arch == X86_64 and n.driver == "kvm" and n.cpu_mode == "host-passthrough"
        assert n.boot_timeout is None


def test_resolve_x86_nokvm_is_display_safe_not_raising():
    # resolve must not raise on missing KVM (status must work); driver falls to qemu
    assert p.resolve(TOPO, X86_NOKVM)["obs"].driver == "qemu"


def test_require_native_kvm_raises_without_kvm():
    with pytest.raises(p.PlatformError):
        p.require_native_kvm(X86_NOKVM)
    p.require_native_kvm(X86_KVM)  # no raise


def test_resolve_guards_arm64_on_x86():
    topo = {**TOPO, "nodes": {"weird": {"platform": "ubuntu2404-arm64"}}}
    with pytest.raises(p.PlatformError, match="ARM on x86"):
        p.resolve(topo, X86_KVM)


def test_resolve_rejects_unknown_platform():
    topo = {**TOPO, "nodes": {"x": {"platform": "nope"}}}
    with pytest.raises(p.PlatformError, match="unknown platform"):
        p.resolve(topo, X86_KVM)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_platforms.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.platforms'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/platforms.py
"""Resolve each topology node to a concrete libvirt provider config, given host facts (#276).

The single authority for the host-arch-gated virtualization matrix (design §4.2).
Pure and display-safe: callers pass HostFacts, resolve() never raises on missing KVM
(so status works); the hard native-KVM gate is require_native_kvm(), called by the
bring-up path. resolve() raises only on the structurally-impossible arm64-on-x86 (D4)
or an undefined platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mqlab.hostfacts import AARCH64, X86_64, HostFacts

AAVMF_LOADER = "/usr/share/AAVMF/AAVMF_CODE.fd"


class PlatformError(RuntimeError):
    """A node cannot be resolved to a runnable provider config."""


@dataclass(frozen=True)
class ResolvedNode:
    platform: str
    box: str
    arch: str
    driver: str
    machine_arch: str | None
    machine_type: str | None
    loader: str | None
    nvram: str | None
    input_bus: str | None
    cpu_mode: str
    boot_timeout: int | None
    cpus: int
    memory: int
    extra_disk: int | None
    dvd: str | None
    nics: dict[str, str]


def default_platform(facts: HostFacts) -> str:
    """Native-preferred Ubuntu platform for this host (D1/D6)."""
    return "ubuntu2404-x86_64" if facts.arch == X86_64 else "ubuntu2404-arm64"


def require_native_kvm(facts: HostFacts) -> None:
    """Hard native-KVM requirement (D3). The native arch always equals the host arch."""
    if not facts.kvm:
        raise PlatformError(
            "native KVM required: /dev/kvm is unavailable for this host. Enable nested "
            "virtualization / KVM. (mqlab does not run the native architecture under TCG.)"
        )


def resolve(topo: dict[str, Any], facts: HostFacts) -> dict[str, ResolvedNode]:
    boxes = topo["boxes"]
    defaults = topo.get("defaults", {})
    out: dict[str, ResolvedNode] = {}
    for name, raw in topo.get("nodes", {}).items():
        spec = raw or {}
        platform = spec.get("platform", default_platform(facts))
        if platform not in boxes:
            raise PlatformError(f"node {name}: unknown platform {platform!r}")
        out[name] = _provider(name, spec, defaults, platform, boxes[platform], facts)
    return out


def _provider(name, spec, defaults, platform, box, facts) -> ResolvedNode:  # noqa: PLR0913
    guest = box["arch"]
    if guest == AARCH64 and facts.arch == X86_64:
        raise PlatformError(f"node {name}: emulating ARM on x86 is unsupported")
    kvm = guest == facts.arch and facts.kvm
    is_arm = guest == AARCH64
    return ResolvedNode(
        platform=platform,
        box=box["box"],
        arch=guest,
        driver="kvm" if kvm else "qemu",
        machine_arch=None if is_arm else X86_64,
        machine_type=None if is_arm else "q35",
        loader=AAVMF_LOADER if is_arm else None,
        nvram=f"/var/lib/libvirt/qemu/nvram/lab-{name}_VARS.fd" if is_arm else None,
        input_bus="virtio" if is_arm else None,
        cpu_mode="host-passthrough" if kvm else "maximum",
        boot_timeout=None if kvm else 1800,
        cpus=spec.get("cpus", defaults.get("cpus", 1)),
        memory=spec.get("memory", defaults.get("memory", 1024)),
        extra_disk=spec.get("extra_disk"),
        dvd=box.get("dvd"),
        nics=spec.get("nics", {}),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_platforms.py -q`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/platforms.py tests/test_platforms.py
vrg-commit --type feat --scope platforms --message "host-arch resolution matrix + native-KVM gate (#276)"
```

---

### Task 3: Resolved-topology render + `ensure_resolved`

**Files:**
- Modify: `src/mqlab/paths.py`
- Modify: `src/mqlab/platforms.py` (append render/ensure functions)
- Test: `tests/test_platforms.py` (append), `tests/test_paths.py` (append)

**Interfaces:**
- Consumes: `mqlab.paths.{repo_root, resolved_topology_path}`, `mqlab.hostfacts.probe`, `resolve`, `require_native_kvm`.
- Produces: `paths.resolved_topology_path()->Path` (= `repo_root()/"build"/"lab"/"topology.resolved.yaml"`); `platforms.render_resolved(topo, facts)->str` (YAML with top-level `nodes:` mapping name→field dict); `platforms.ensure_resolved(*, facts=None, topo=None)->Path` (calls `require_native_kvm`, renders, writes, returns path).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py  (append)
def test_resolved_topology_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    from mqlab.paths import resolved_topology_path

    assert resolved_topology_path() == tmp_path / "build" / "lab" / "topology.resolved.yaml"
```

```python
# tests/test_platforms.py  (append)
import yaml

from mqlab.hostfacts import HostFacts


def test_render_resolved_emits_nodes_yaml():
    text = p.render_resolved(TOPO, ARM_KVM)
    doc = yaml.safe_load(text)
    assert doc["nodes"]["obs"]["driver"] == "kvm"
    assert doc["nodes"]["rdqm-a1"]["boot_timeout"] == 1800


def test_ensure_resolved_writes_file_and_requires_kvm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    out = p.ensure_resolved(facts=X86_KVM, topo=TOPO)
    assert out.exists()
    assert yaml.safe_load(out.read_text())["nodes"]["obs"]["arch"] == X86_64
    with pytest.raises(p.PlatformError):
        p.ensure_resolved(facts=X86_NOKVM, topo=TOPO)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py::test_resolved_topology_path tests/test_platforms.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolved_topology_path'` / `AttributeError: module 'mqlab.platforms' has no attribute 'render_resolved'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/paths.py  (add function)
def resolved_topology_path() -> Path:
    """Where the host-resolved topology is rendered for the Vagrantfile (#276)."""
    return repo_root() / "build" / "lab" / "topology.resolved.yaml"
```

```python
# src/mqlab/platforms.py  (append; add imports at top)
from dataclasses import asdict

import yaml

from mqlab.hostfacts import probe
from mqlab.paths import repo_root, resolved_topology_path


def render_resolved(topo: dict[str, Any], facts: HostFacts) -> str:
    nodes = {name: asdict(node) for name, node in resolve(topo, facts).items()}
    return yaml.safe_dump({"nodes": nodes}, sort_keys=True)


def ensure_resolved(*, facts: HostFacts | None = None, topo: dict[str, Any] | None = None):
    """Render build/lab/topology.resolved.yaml. Enforces the native-KVM gate (D3)."""
    facts = facts if facts is not None else probe()
    require_native_kvm(facts)
    if topo is None:
        topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    path = resolved_topology_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_resolved(topo, facts))
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py tests/test_platforms.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/paths.py src/mqlab/platforms.py tests/test_platforms.py tests/test_paths.py
vrg-commit --type feat --scope platforms --message "render build/lab/topology.resolved.yaml + ensure_resolved (#276)"
```

---

### Task 4: Host-aware default in `topology.yaml` + `fleet.lab_guests`

**Files:**
- Modify: `lab/topology.yaml`
- Modify: `src/mqlab/fleet.py`
- Test: `tests/test_fleet.py` (modify), `tests/test_vmstatus.py` (modify)

**Interfaces:**
- Consumes: `mqlab.platforms.default_platform`, `mqlab.hostfacts.{HostFacts, probe}`.
- Produces: `fleet.lab_guests(facts: HostFacts | None = None) -> dict[str, str]` — name→platform, applying `default_platform(facts)` (default `probe()`) for nodes without an explicit platform. `fleet.DEFAULT_PLATFORM` is removed.

- [ ] **Step 1: Modify `lab/topology.yaml`**

Replace the `boxes:`/`defaults:` block:

```yaml
boxes:
  ubuntu2404-arm64:  { box: cloud-image/ubuntu-24.04, arch: aarch64 }
  ubuntu2404-x86_64: { box: cloud-image/ubuntu-24.04, arch: x86_64 }
  alma9-x86_64:      { box: almalinux/9,               arch: x86_64 }
  rhel96-x86_64:
    box: rhel/9.6-x86_64
    arch: x86_64
    dvd: /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso

# No defaults.platform: the default Ubuntu platform is host-resolved by mqlab
# (default_platform(facts)) so guests track the host arch (native-preferred, #276).
defaults: { cpus: 1, memory: 1024 }
```

(Leave every explicit `platform: rhel96-x86_64` on nodes as-is.)

- [ ] **Step 2: Write the failing test (modify `tests/test_fleet.py`)**

Replace the two `lab_guests` tests with fact-injected versions:

```python
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

ARM = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)


def test_lab_guests_default_tracks_host(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "defaults: { cpus: 1 }\nnodes:\n  rdqm-a1: { platform: rhel96-x86_64 }\n  pcmk-a1: {}\n"
    )
    assert lab_guests(ARM) == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-arm64"}
    assert lab_guests(X86) == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-x86_64"}
```

(Update `test_fleet_rows_*` platform literals from `ubuntu2404-arm64` to whatever the test passes in `platforms=`; those tests pass `platforms` explicitly so they need no host logic — just keep them internally consistent.)

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_fleet.py -q`
Expected: FAIL — `lab_guests()` takes no argument / `ImportError` for `default_platform` not yet wired.

- [ ] **Step 4: Modify `src/mqlab/fleet.py`**

```python
# remove: DEFAULT_PLATFORM = "ubuntu2404-arm64"
# add imports:
from mqlab.hostfacts import HostFacts, probe
from mqlab.platforms import default_platform


def lab_guests(facts: HostFacts | None = None) -> dict[str, str]:
    """guest name -> platform, from topology.yaml; the default Ubuntu platform is
    host-resolved (native-preferred, #276)."""
    facts = facts if facts is not None else probe()
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    default = default_platform(facts)
    nodes = data.get("nodes", {})
    return {name: (cfg or {}).get("platform", default) for name, cfg in nodes.items()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_fleet.py tests/test_vmstatus.py -q`
Expected: PASS. (If `test_vmstatus.py` calls `lab_guests()` with the real arm64 dev host, it still returns arm64 platforms — adjust its inline topology to drop `defaults.platform` and, if it asserts a platform, inject `HostFacts`.)

- [ ] **Step 6: Commit**

```bash
vrg-git add lab/topology.yaml src/mqlab/fleet.py tests/test_fleet.py tests/test_vmstatus.py
vrg-commit --type feat --scope fleet --message "host-aware default platform; add ubuntu2404-x86_64 (#276)"
```

---

### Task 5: Thread facts through `manifest` (#266 integration)

**Files:**
- Modify: `src/mqlab/manifest.py`
- Test: `tests/test_manifest.py` (modify)

**Interfaces:**
- Consumes: `mqlab.hostfacts.{HostFacts, probe}`, `fleet.lab_guests`.
- Produces: `manifest._ARCH_SUFFIX` gains `"ubuntu2404-x86_64": "UbuntuLinuxX64"`; `manifest.setup_platforms(setup: str, facts: HostFacts | None = None) -> set[str]` passes facts to `lab_guests`.

- [ ] **Step 1: Write the failing test (modify `tests/test_manifest.py`)**

```python
from mqlab.hostfacts import AARCH64, X86_64, HostFacts


def test_tarball_name_x86_ubuntu():
    from mqlab import manifest as m

    assert m.tarball_name("9.4.5.0", "ubuntu2404-x86_64") == (
        "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz"
    )


def test_setup_platforms_threads_facts(monkeypatch):
    from mqlab import manifest as m

    x86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
    seen = {}
    monkeypatch.setattr(m, "lab_guests", lambda facts=None: seen.setdefault("facts", facts) or {"n1": "ubuntu2404-x86_64"})
    monkeypatch.setattr(m, "_topology", lambda: {"groups": {"g": ["n1"]}, "setups": {"s": {"groups": ["g"]}}})
    assert m.setup_platforms("s", x86) == {"ubuntu2404-x86_64"}
    assert seen["facts"] is x86
```

(Update the existing `test_setup_platforms`/`box_version_pins` fixtures that hardcode `ubuntu2404-arm64` to also work fact-injected; where they call `setup_platforms("s")` with no facts on the arm64 dev host, the arm64 result is still correct — only add an explicit-facts assertion for x86.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: FAIL — `KeyError`/`ValueError` for the x86 suffix, and `setup_platforms` takes one arg.

- [ ] **Step 3: Modify `src/mqlab/manifest.py`**

```python
# _ARCH_SUFFIX: add the x86_64 Ubuntu entry
_ARCH_SUFFIX = {
    "ubuntu2404-arm64": "UbuntuLinuxARM64",
    "ubuntu2404-x86_64": "UbuntuLinuxX64",
    "rhel96-x86_64": "LinuxX64",
    "alma9-x86_64": "LinuxX64",
}
```

```python
# setup_platforms: accept and thread facts
from mqlab.hostfacts import HostFacts  # add to imports


def setup_platforms(setup: str, facts: HostFacts | None = None) -> set[str]:
    """Distinct guest platforms in a setup — the MQ tarballs it needs (host-resolved, #276)."""
    topo = _topology()
    groups = topo.get("groups") or {}
    platforms = lab_guests(facts)
    setup_groups = (topo.get("setups") or {}).get(setup, {}).get("groups") or []
    nodes = {n for g in setup_groups for n in (groups.get(g) or [])}
    return {platforms[n] for n in nodes if n in platforms}
```

Also update `artifact.ensure_mq_tarballs` to accept `facts` and pass it through:

```python
# src/mqlab/artifact.py
from mqlab.hostfacts import HostFacts  # add


def ensure_mq_tarballs(setup, mq_version, build_mq_dir, *, fetch, facts: HostFacts | None = None):
    paths = []
    for platform in sorted(setup_platforms(setup, facts)):
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_manifest.py tests/test_artifact.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/manifest.py src/mqlab/artifact.py tests/test_manifest.py tests/test_artifact.py
vrg-commit --type feat --scope manifest --message "thread host facts; add ubuntu2404-x86_64 MQ suffix (#266, #276)"
```

---

### Task 6: Rewrite the Vagrantfile to consume the resolved file

**Files:**
- Modify: `lab/Vagrantfile`
- Test: `tests/test_platforms.py` (append a contract test asserting the rendered keys the Vagrantfile reads)

**Interfaces:**
- Consumes: `build/lab/topology.resolved.yaml` (per-node fields from `ResolvedNode`) and `build/box-versions.json` (#266, by `platform`).
- The Vagrantfile has no unit test (Ruby); the contract test guards the field names it depends on.

- [ ] **Step 1: Write the failing contract test (`tests/test_platforms.py` append)**

```python
def test_resolved_node_has_every_vagrantfile_field():
    node = p.resolve(TOPO, ARM_KVM)["obs"]
    required = {
        "platform", "box", "driver", "cpu_mode", "machine_arch", "machine_type",
        "loader", "nvram", "input_bus", "boot_timeout", "cpus", "memory",
        "extra_disk", "dvd", "nics",
    }
    from dataclasses import asdict

    assert required <= set(asdict(node))
```

- [ ] **Step 2: Run it (passes already — it documents the contract before the Ruby edit)**

Run: `uv run pytest tests/test_platforms.py::test_resolved_node_has_every_vagrantfile_field -q`
Expected: PASS (ResolvedNode already carries these from Task 2). This locks the contract.

- [ ] **Step 3: Rewrite `lab/Vagrantfile`**

```ruby
# lab/Vagrantfile — multi-machine lab driven by mqlab's host-resolved topology (#276).
#
# Provider mechanics (driver/firmware/cpu_mode) are chosen by mqlab's resolver from
# the host architecture and rendered to build/lab/topology.resolved.yaml. This file
# is a dumb consumer: it applies fields verbatim. Box-version pins still come from
# build/box-versions.json (#266).
require "yaml"
require "json"

resolved = File.join(__dir__, "..", "build", "lab", "topology.resolved.yaml")
unless File.exist?(resolved)
  raise "resolved topology missing: #{resolved}\n" \
        "Run `mqlab vm up` / `mqlab vm create` (they render it from lab/topology.yaml " \
        "+ host facts via `mqlab vm render`)."
end
nodes = YAML.load_file(resolved).fetch("nodes")

bvf = File.join(__dir__, "..", "build", "box-versions.json")
box_versions = File.exist?(bvf) ? JSON.parse(File.read(bvf)) : {}

Vagrant.configure("2") do |config|
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.ssh.insert_key = false

  nodes.each do |name, n|
    config.vm.define name do |node|
      node.vm.box      = n.fetch("box")
      bv = box_versions[n.fetch("platform")]
      node.vm.box_version = bv if bv
      node.vm.hostname = name
      node.vm.boot_timeout = n["boot_timeout"] if n["boot_timeout"]
      node.vm.provider :libvirt do |lv|
        lv.cpus     = n.fetch("cpus")
        lv.memory   = n.fetch("memory")
        lv.driver   = n.fetch("driver")
        lv.cpu_mode = n.fetch("cpu_mode")
        lv.machine_arch = n["machine_arch"] if n["machine_arch"]
        lv.machine_type = n["machine_type"] if n["machine_type"]
        lv.loader   = n["loader"] if n["loader"]
        lv.nvram    = n["nvram"]  if n["nvram"]
        lv.input(:type => "mouse", :bus => n["input_bus"]) if n["input_bus"]
        lv.storage(:file, :size => "#{n['extra_disk']}G") if n["extra_disk"]
        if n["dvd"]
          lv.storage :file, :device => :cdrom, :path => n["dvd"], :bus => "sata"
        end
      end
      (n["nics"] || {}).each do |net, ip|
        node.vm.network :private_network,
          :libvirt__network_name => net, :ip => ip, :libvirt__dhcp_enabled => false
      end
    end
  end
end
```

- [ ] **Step 4: Sanity-check Ruby parses**

Run: `ruby -c lab/Vagrantfile`
Expected: `Syntax OK`.

- [ ] **Step 5: Commit**

```bash
vrg-git add lab/Vagrantfile tests/test_platforms.py
vrg-commit --type feat --scope lab --message "Vagrantfile consumes host-resolved topology; drop arch case (#276)"
```

---

### Task 7: `doctor` preflight module

**Files:**
- Create: `src/mqlab/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `mqlab.hostfacts.HostFacts`, `mqlab.platforms.require_native_kvm`.
- Produces: `@dataclass(frozen=True) Check(name:str, ok:bool, detail:str, fix:str|None)`; `run_checks(facts, *, which: Callable[[str], str|None]) -> list[Check]`; `INSTALL_HINTS: dict[str, dict[str, str]]` (per distro family); `summarise(checks) -> tuple[bool, str]`.
- Behaviour: if `facts.in_vergil` → a single passing `Check("vergil", True, ...)`. Else: native-KVM check (hard), required-tool checks via injected `which`, each missing tool yields `fix` = install hint for `facts.distro_family`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor.py
from __future__ import annotations

from mqlab import doctor as d
from mqlab.hostfacts import AARCH64, X86_64, HostFacts

VERGIL = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
X86 = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=False)
X86_NOKVM = HostFacts(arch=X86_64, kvm=False, distro_family="dnf", in_vergil=False)

ALL_PRESENT = lambda name: f"/usr/bin/{name}"  # noqa: E731
NONE_PRESENT = lambda name: None  # noqa: E731


def test_vergil_short_circuits():
    checks = d.run_checks(VERGIL, which=ALL_PRESENT)
    assert [c.name for c in checks] == ["vergil"]
    assert checks[0].ok
    assert d.summarise(checks) == (True, d.summarise(checks)[1])


def test_x86_all_present_passes():
    ok, _ = d.summarise(d.run_checks(X86, which=ALL_PRESENT))
    assert ok is True


def test_x86_missing_kvm_hard_fails():
    checks = d.run_checks(X86_NOKVM, which=ALL_PRESENT)
    kvm = next(c for c in checks if c.name == "kvm")
    assert kvm.ok is False
    assert d.summarise(checks)[0] is False


def test_missing_tool_yields_dnf_install_hint():
    checks = d.run_checks(X86, which=NONE_PRESENT)
    virsh = next(c for c in checks if c.name == "virsh")
    assert virsh.ok is False
    assert "dnf install" in virsh.fix


def test_missing_tool_yields_apt_hint_on_ubuntu():
    ubuntu = HostFacts(arch=X86_64, kvm=True, distro_family="apt", in_vergil=False)
    checks = d.run_checks(ubuntu, which=NONE_PRESENT)
    virsh = next(c for c in checks if c.name == "virsh")
    assert "apt install" in virsh.fix


def test_arm_host_requires_qemu_aarch64_too():
    arm = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=False)
    names = {c.name for c in d.run_checks(arm, which=ALL_PRESENT)}
    assert "qemu-system-aarch64" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.doctor'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/doctor.py
"""Host preflight: enforce the native-KVM requirement and diagnose missing tools (#276).

Diagnoses and suggests; never installs (D5). Pure given an injected `which`, so the
checklist matrix is testable. Inside Vergil the profile guarantees prerequisites, so
the check short-circuits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mqlab.hostfacts import AARCH64, HostFacts

# tool name -> package name per distro family
_PACKAGES = {
    "qemu-system-x86_64": {"apt": "qemu-system-x86", "dnf": "qemu-kvm"},
    "qemu-system-aarch64": {"apt": "qemu-system-arm", "dnf": "qemu-kvm"},
    "virsh": {"apt": "libvirt-clients", "dnf": "libvirt-client"},
    "vagrant": {"apt": "vagrant", "dnf": "vagrant"},
    "ansible": {"apt": "ansible", "dnf": "ansible-core"},
    "genisoimage": {"apt": "genisoimage", "dnf": "genisoimage"},
}
_INSTALLER = {"apt": "sudo apt install -y", "dnf": "sudo dnf install -y"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None


def _required_tools(facts: HostFacts) -> list[str]:
    tools = ["qemu-system-x86_64", "virsh", "vagrant", "ansible", "genisoimage"]
    if facts.arch == AARCH64:
        tools.insert(1, "qemu-system-aarch64")
    return tools


def _install_hint(tool: str, family: str) -> str | None:
    installer = _INSTALLER.get(family)
    pkg = _PACKAGES[tool].get(family)
    if installer is None or pkg is None:
        return None
    return f"{installer} {pkg}"


def run_checks(facts: HostFacts, *, which: Callable[[str], str | None]) -> list[Check]:
    if facts.in_vergil:
        return [Check("vergil", True, "Vergil-managed host; prerequisites guaranteed by the profile")]
    checks = [
        Check("kvm", facts.kvm, "native /dev/kvm usable" if facts.kvm else "native KVM unavailable",
              None if facts.kvm else "enable nested virtualization / KVM for this host"),
    ]
    for tool in _required_tools(facts):
        present = which(tool) is not None
        checks.append(Check(
            tool, present, "found" if present else "missing",
            None if present else _install_hint(tool, facts.distro_family),
        ))
    return checks


def summarise(checks: list[Check]) -> tuple[bool, str]:
    ok = all(c.ok for c in checks)
    lines = [f"[{'ok ' if c.ok else 'FAIL'}] {c.name}: {c.detail}" + (f"  -> {c.fix}" if c.fix else "")
             for c in checks]
    return ok, "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/doctor.py tests/test_doctor.py
vrg-commit --type feat --scope doctor --message "host preflight checklist with native-KVM gate + install hints (#276)"
```

---

### Task 8: CLI wiring — `mqlab doctor`, and gate every vagrant verb

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_doctor.py` (new); `tests/test_cli_vm.py` (append)

**Interfaces:**
- Consumes: `doctor.{run_checks, summarise}`, `hostfacts.probe`, `platforms.ensure_resolved`, `shutil.which`.
- Produces: `mqlab doctor` command (exit 0 on all-pass, exit 1 otherwise, prints `summarise`); a private `_prepare_lab()` helper that runs `ensure_resolved()` (and, outside Vergil, the doctor hard-gate) — called at the start of every CLI verb that shells `vagrant` (`vm create`, `vm up`, `vm down`, `vm destroy`, `vm status`, `vm ssh`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_doctor.py
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
    monkeypatch.setattr(cli, "_doctor_checks", lambda: [Check("kvm", False, "no kvm", "enable KVM")])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "enable KVM" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_doctor.py -q`
Expected: FAIL — no `doctor` command / no `_doctor_checks`.

- [ ] **Step 3: Implement in `src/mqlab/cli.py`**

Add near the other imports and helpers:

```python
import shutil

from mqlab.doctor import run_checks, summarise
from mqlab.hostfacts import probe
from mqlab.platforms import PlatformError, ensure_resolved


def _doctor_checks():
    return run_checks(probe(), which=shutil.which)


def _prepare_lab() -> None:
    """Gate + render before any vagrant call: enforce prerequisites, write the
    resolved topology. Fail loud (#276)."""
    facts = probe()
    if not facts.in_vergil:
        ok, report = summarise(run_checks(facts, which=shutil.which))
        if not ok:
            typer.echo(report)
            raise typer.Exit(code=1)
    ensure_resolved(facts=facts)  # raises PlatformError on missing native KVM


@app.command("doctor")
def doctor() -> None:
    """Check this host can run the lab (arch, KVM, required tools)."""
    ok, report = summarise(_doctor_checks())
    typer.echo(report)
    raise typer.Exit(code=0 if ok else 1)
```

Then call `_prepare_lab()` as the first line of each vagrant-shelling verb body (`vm create`, `vm up`, `vm down`, `vm destroy`, `vm status`, `vm ssh`). Example for `vm up` (around `cli.py:444`):

```python
@vm_app.command("up")
def vm_up(pattern: str, ...) -> None:
    _prepare_lab()
    ...  # existing body
```

- [ ] **Step 4: Append a gate test to `tests/test_cli_vm.py`**

```python
def test_vm_up_runs_prepare_lab(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "_prepare_lab", lambda: called.setdefault("yes", True))
    # ... invoke `vm up` with the existing fakes; assert called["yes"] is True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_doctor.py tests/test_cli_vm.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_doctor.py tests/test_cli_vm.py
vrg-commit --type feat --scope cli --message "mqlab doctor + render/gate before every vagrant verb (#276)"
```

---

### Task 9: MQ install roles — derive the Ubuntu suffix from `ansible_architecture`

**Files:**
- Modify: `ansible/roles/mq-install/tasks/main.yml`
- Modify: `ansible/roles/mq-client/tasks/main.yml`

**Interfaces:** none (Ansible). Verified by the grep backstop and Tier-1 acceptance.

- [ ] **Step 1: Edit both roles**

In each role, change the `src:` of the "copy MQ tar to node" task from the hardcoded `…UbuntuLinuxARM64.tar.gz` to:

```yaml
    src: "{{ playbook_dir }}/../build/mq/{{ mq_version }}-IBM-MQ-Advanced-for-Developers-UbuntuLinux{{ 'ARM64' if ansible_architecture == 'aarch64' else 'X64' }}.tar.gz"
```

- [ ] **Step 2: Grep backstop — prove no other `UbuntuLinuxARM64` literal survives**

Run: `grep -rn "UbuntuLinuxARM64" ansible/ scripts/ src/ lab/`
Expected: only matches inside an `ansible_architecture` conditional (the two roles above) or in `manifest._ARCH_SUFFIX` (the legitimate per-platform map) — no bare hardcoded copy `src:`.

- [ ] **Step 3: Lint the YAML via the gate**

Run: `vrg-container-run -- vrg-validate` (ansible-lint runs here).
Expected: green.

- [ ] **Step 4: Commit**

```bash
vrg-git add ansible/roles/mq-install/tasks/main.yml ansible/roles/mq-client/tasks/main.yml
vrg-commit --type fix --scope ansible --message "mq-install/mq-client: arch-derive the Ubuntu MQ tar suffix (#276)"
```

---

### Task 10: `fetch-mq.sh` — fetch host-arch Ubuntu + LinuxX64

**Files:**
- Modify: `scripts/fetch-mq.sh`

- [ ] **Step 1: Rewrite the fetch loop**

```bash
#!/usr/bin/env bash
# scripts/fetch-mq.sh - download IBM MQ Advanced for Developers (no-charge) into
# gitignored build/mq/. Fetches the host-arch Ubuntu deb tarball AND the x86_64
# RHEL/RDQM tarball (LinuxX64), so a fresh clone has both arms' artifacts (#276).
# Never commit these binaries.
set -euo pipefail
VER="9.4.5.0"
BASE="https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv"
DEST="$(cd "$(dirname "$0")/.." && pwd)/build/mq"
mkdir -p "$DEST"

case "$(uname -m)" in
  aarch64|arm64) UBU="UbuntuLinuxARM64" ;;
  x86_64|amd64)  UBU="UbuntuLinuxX64" ;;
  *) echo "unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac

# Ubuntu (host arch) for the pcmk/obs/app arm; LinuxX64 (x86_64) for the RHEL/RDQM arm.
for SUFFIX in "$UBU" "LinuxX64"; do
  TAR="${VER}-IBM-MQ-Advanced-for-Developers-${SUFFIX}.tar.gz"
  if [ ! -f "$DEST/$TAR" ]; then
    curl -fL --retry 3 -o "$DEST/$TAR.part" "$BASE/$TAR"
    mv "$DEST/$TAR.part" "$DEST/$TAR"
  fi
  if [ -f "$DEST/$TAR.sha256" ]; then
    (cd "$DEST" && sha256sum -c "$TAR.sha256")
  else
    (cd "$DEST" && sha256sum "$TAR" > "$TAR.sha256")
    echo "recorded checksum: $(cat "$DEST/$TAR.sha256")"
  fi
  echo "ok: $DEST/$TAR"
done
```

- [ ] **Step 2: Shellcheck via the gate**

Run: `vrg-container-run -- vrg-validate` (shellcheck runs here).
Expected: green.

- [ ] **Step 3: Commit**

```bash
vrg-git add scripts/fetch-mq.sh
vrg-commit --type fix --scope scripts --message "fetch-mq: host-arch Ubuntu tar + LinuxX64 (#276)"
```

---

### Task 11: Consumer audit, docs, full validate, Tier-1 acceptance

**Files:**
- Modify: `docs/development/lab-bringup-capture.md`
- Create: `docs/reports/2026-06-18-x86-portability-consumer-audit.md` (the §5.1 verdict table)

- [ ] **Step 1: Execute the §5.1 consumer audit**

Run: `grep -rln "lab_guests\|boxes\|defaults\|\.get(\"platform\"\|setup_platforms" src/mqlab/*.py`
For each of `arms, cli, dashboard, fleet, guestsel, inventory, netstate, parity, roster, scrape, setups, vmstatus, manifest, artifact`, record a one-line verdict (name-only / arch-aware / facts-threaded) in `docs/reports/2026-06-18-x86-portability-consumer-audit.md`. Fix any module that reads `defaults["platform"]` as a concrete key (now removed) or `boxes[*].arch` outside the resolver — make it route through `lab_guests(facts)` / `resolve`.

- [ ] **Step 2: Update `docs/development/lab-bringup-capture.md`**

Replace the two MQ-artifact rows to state both tarballs are fetched by `scripts/fetch-mq.sh` keyed on host arch (Ubuntu `ARM64`|`X64`) plus `LinuxX64`, and note `mqlab doctor` / the auto-rendered `build/lab/topology.resolved.yaml`.

- [ ] **Step 3: Full validation gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green — ruff, mypy, ansible-lint, shellcheck, markdownlint, and pytest at **100% branch coverage**. If coverage < 100%, add the missing branch test (commonly the `os_release.exists()` false branch in `hostfacts.probe`, or a `_install_hint` `None` path in `doctor`).

- [ ] **Step 4: Commit the docs/audit**

```bash
vrg-git add docs/development/lab-bringup-capture.md docs/reports/2026-06-18-x86-portability-consumer-audit.md
vrg-commit --type docs --scope arch --message "consumer audit + bringup-capture for host-arch portability (#276)"
```

- [ ] **Step 5: Tier-1 acceptance (human-run, arm64 Mac) — do no harm**

This is the blocking near-term gate (spec §9 Tier 1). Run from the Vergil dev VM:

```bash
mqlab doctor                     # expect: Vergil-managed; pass
mqlab vm destroy all             # clean slate
mqlab vm up pcmk_san_ha          # arm64 Ubuntu (KVM) + the resolved file path
mqlab vm up rdqm_ha              # x86_64 RHEL (TCG) — unchanged behaviour
```

Expected: a one-pass cold bring-up with **no behavioural change** from before this work — Ubuntu guests on KVM, RHEL guests on TCG, `build/lab/topology.resolved.yaml` present, `vagrant status` clean. Capture the result. Tier-2 (real x86 host, native KVM) remains deferred per §9 and §10 and is what closes the issue.

---

## Self-Review

**Spec coverage:** D1 (host-default platform) → Tasks 2, 4. D2 (single Python authority + resolved file) → Tasks 2, 3, 6. D3 (hard native KVM) → Tasks 2 (`require_native_kvm`), 3, 7, 8. D4 (arm64-on-x86 guard) → Task 2. D5 (doctor diagnoses, never installs) → Tasks 7, 8. D6 (integrate with #266) → Tasks 5, 6, 9, 10. §6 preflight → Tasks 7, 8. §7 artifacts → Tasks 5, 9, 10. §5.1 consumer audit → Task 11. §9 acceptance Tier 1 → Task 11 Step 5; Tier 2 remains an open item. Confirmed facts (`/etc/vergil`, `UbuntuLinuxX64`, multi-arch box) baked into Tasks 1, 5, 4.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the two genuinely-external verifications (tarball URL, box arch) were already confirmed pre-plan and are encoded as constants.

**Type consistency:** `HostFacts(arch, kvm, distro_family, in_vergil)` is constructed identically in every test and module. `ResolvedNode` field set in Task 2 matches the Vagrantfile reads (Task 6 contract test) and the render in Task 3. `lab_guests(facts=None)` (Task 4) and `setup_platforms(setup, facts=None)` (Task 5) signatures match their call sites in `_prepare_lab`/manifest/artifact. `default_platform` / `require_native_kvm` / `ensure_resolved` names are consistent across Tasks 2, 3, 4, 8.
