# Version Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an arm build assert its drivable dependency versions from a per-setup manifest, acquire the MQ artifact, and have every run report capture the real, complete installed stack.

**Architecture:** A pure-Python core in `mqlab` (load/validate the manifest, resolve selection, render an Ansible vars overlay, resolve + acquire the MQ tarball, persist the selection) drives the existing roles/Vagrantfile; a topology-aware Ansible gather play feeds `runreport`'s `read_versions()` so reports carry both the asked-for manifest id and the discovered stack. No verify/reconcile engine.

**Tech Stack:** Python 3.12 (mqlab CLI, `uv`/`pytest`, 100% branch coverage), Ansible (`ansible.builtin` only; validated functionally — no ansible-lint), Vagrant/libvirt, YAML manifests.

**Spec:** `docs/specs/2026-06-18-version-manifest-design.md` (#266).

## Global Constraints

- **Drive what's drivable, discover the rest — no verify/reconcile engine.** A correct pin is a static artifact.
- **Two manifest namespaces:** per-setup SUT `manifests/<setup>/default.yaml` (MQ/HA/OS) + shared `manifests/_shared/observability.yaml` (read by `mqlab obs up`).
- **Scope B keys only, drivable-only:** `mq.version`; `os.box`/`os.box_version`; obs `prometheus`/`node_exporter`/`loki`/`alloy`/`grafana`/`mq_metric_samples_ref`. Derived versions (RHEL HA bundled in MQ, drbd kmod) are **never** pinned — discovered only.
- **Selection pinned at `create`, auto-threaded;** mid-lifecycle change is an explicit error.
- **MQ acquisition:** cache → download → fail-loud-if-fetch-fails, `.sha256` verified on acquire (integrity, not version-reconciliation). The MQ dev tarball is downloadable for **both** arches; the un-fetchable artifact is the **RHEL OS box** (Vagrant's concern + #269), *not* the MQ tarball — so MQ acquisition has no RHEL special-case.
- **No silent failures:** discovery readers MUST raise on a genuine query failure; an expected absence is simply not probed (topology-aware).
- **Defaults preserve current behavior:** absent a manifest/overlay, every var defaults to today's hardcoded value (MQ `9.4.5.0`, the existing obs `_version`s), so nothing breaks before manifests exist.
- **Validation:** `vrg-container-run -- vrg-validate` is the only validation; Ansible/Vagrant changes are proven by running the lab.
- **Manifest keyed by *setup* name** (the `mqlab vm create <setup>` argument), matching the spec's `manifests/distributed-pcmk-ubuntu/` example.

## File Structure

- **Create** `src/mqlab/manifest.py` — load/validate manifests, selection resolution, `setup_platforms`, `tarball_name`, `vars_overlay`, selection persistence. One responsibility: turn a (setup, name) into validated drive inputs.
- **Create** `src/mqlab/artifact.py` — MQ tarball acquisition (cache/download/verify), injectable fetcher.
- **Create** `manifests/_shared/observability.yaml`, `manifests/distributed-pcmk-ubuntu/default.yaml`, `manifests/distributed-rdqm-rhel/default.yaml`.
- **Create** `ansible/gather-versions.yml` — topology-aware version gather play.
- **Create** `ansible/group_vars/all/versions.yml` — defaults for the manifest-driven vars (preserve current behavior).
- **Modify** `src/mqlab/paths.py` — manifest + selection-state path helpers.
- **Modify** `src/mqlab/runreport.py` — `read_versions()` reads the gathered JSON; `RunMetadata`/`capture_metadata` gain a `manifest` field.
- **Modify** `src/mqlab/cli.py` — `--manifest` on create/provision/qm/obs; resolve+persist+acquire at create; inject `-e @overlay` into the `ansible-playbook` steps; read selection downstream.
- **Modify** `ansible/roles/mq-install/tasks/main.yml`, `ansible/roles/mq-client/tasks/main.yml`, `ansible/roles/rdqm-install/tasks/main.yml` — tarball name from `{{ mq_version }}`.
- **Modify** `ansible/roles/grafana/tasks/main.yml` — apt version pin from `{{ grafana_version }}`.
- **Modify** `lab/Vagrantfile` — set `node.vm.box_version` from a `build/`-side override.
- **Test** `tests/test_manifest.py`, `tests/test_artifact.py` (new); extend `tests/test_runreport.py`.

---

## Task 1: Manifest core — load, validate, overlay, tarball name, platforms

**Files:**
- Create: `src/mqlab/manifest.py`
- Test: `tests/test_manifest.py`
- Modify: `src/mqlab/paths.py` (add `manifests_root`, `selection_state_path`)

**Interfaces:**
- Consumes: `mqlab.paths.repo_root`, `mqlab.fleet.lab_guests`, `lab/topology.yaml`.
- Produces:
  - `class Manifest(setup: str, name: str, mq_version: str, box: str, box_version: str, observability: dict[str,str])`
  - `load_manifest(setup: str, name: str = "default") -> Manifest`
  - `setup_platforms(setup: str) -> set[str]`
  - `tarball_name(mq_version: str, platform: str) -> str`
  - `vars_overlay(m: Manifest) -> dict[str, str]`

- [ ] **Step 1: Add path helpers**

In `src/mqlab/paths.py`, after `reports_dir()`:
```python
def manifests_root() -> Path:
    """Committed manifest source tree at the repo root."""
    return repo_root() / "manifests"


def selection_state_path(setup: str) -> Path:
    """Where the resolved manifest selection is pinned for a live build (build/ tree)."""
    return repo_root() / "build" / "manifests" / f"{setup}.yaml"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_manifest.py`:
```python
from __future__ import annotations

import pytest

from mqlab import manifest as m


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "manifests_root", lambda: tmp_path / "manifests")
    _write(
        tmp_path,
        "manifests/distributed-pcmk-ubuntu/default.yaml",
        "mq:\n  version: '9.4.5.0'\nos:\n  box: cloud-image/ubuntu-24.04\n  box_version: '20260518.0.0'\n",
    )
    _write(
        tmp_path,
        "manifests/_shared/observability.yaml",
        "prometheus: '2.53.2'\nnode_exporter: '1.8.2'\nloki: '3.1.0'\n"
        "alloy: '1.3.0'\ngrafana: '11.1.0'\nmq_metric_samples_ref: 'v5.6.4'\n",
    )
    return tmp_path


def test_load_manifest_merges_sut_and_shared_obs(manifests):
    man = m.load_manifest("distributed-pcmk-ubuntu")
    assert man.mq_version == "9.4.5.0"
    assert man.box == "cloud-image/ubuntu-24.04"
    assert man.box_version == "20260518.0.0"
    assert man.observability["prometheus"] == "2.53.2"
    assert man.observability["mq_metric_samples_ref"] == "v5.6.4"


def test_load_manifest_missing_required_key_raises(manifests, tmp_path):
    (tmp_path / "manifests/distributed-pcmk-ubuntu/default.yaml").write_text(
        "mq: {}\nos:\n  box: x\n  box_version: y\n"
    )
    with pytest.raises(ValueError, match="missing required key mq.version"):
        m.load_manifest("distributed-pcmk-ubuntu")


def test_load_manifest_missing_file_raises(manifests):
    with pytest.raises(FileNotFoundError, match="nope/default.yaml"):
        m.load_manifest("nope")


def test_tarball_name_maps_version_and_arch():
    assert (
        m.tarball_name("9.4.5.0", "ubuntu2404-arm64")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    )
    assert (
        m.tarball_name("9.4.5.0", "rhel96-x86_64")
        == "9.4.5.0-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
    )


def test_tarball_name_unknown_platform_raises():
    with pytest.raises(ValueError, match="no MQ tarball arch mapping"):
        m.tarball_name("9.4.5.0", "solaris-sparc")


def test_vars_overlay_maps_to_role_var_names(manifests):
    man = m.load_manifest("distributed-pcmk-ubuntu")
    ov = m.vars_overlay(man)
    assert ov["mq_version"] == "9.4.5.0"
    assert ov["lab_box_version"] == "20260518.0.0"
    assert ov["prometheus_version"] == "2.53.2"
    assert ov["mq_exporter_ref"] == "v5.6.4"
```

- [ ] **Step 3: Run the tests, verify they fail**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: FAIL (`ModuleNotFoundError: mqlab.manifest`).

- [ ] **Step 4: Implement `src/mqlab/manifest.py`**

```python
"""Version manifest — turn a (setup, name) into validated drive inputs (#266).

The per-setup SUT manifest (MQ/HA/OS) is merged with the shared obs manifest
(`manifests/_shared/observability.yaml`). Drivable-only: every key maps to a value
we can actually set; derived versions are discovered, never pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from mqlab.fleet import lab_guests
from mqlab.paths import manifests_root, repo_root

# Arch suffix in the MQ-for-Developers tarball name, per VM platform.
_ARCH_SUFFIX = {
    "ubuntu2404-arm64": "UbuntuLinuxARM64",
    "rhel96-x86_64": "LinuxX64",
    "alma9-x86_64": "LinuxX64",
}


@dataclass(frozen=True)
class Manifest:
    setup: str
    name: str
    mq_version: str
    box: str
    box_version: str
    observability: dict[str, str]


def _topology() -> dict[str, Any]:
    return yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())


def _load_yaml(path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} is not a mapping")
    return data


def load_manifest(setup: str, name: str = "default") -> Manifest:
    sut = _load_yaml(manifests_root() / setup / f"{name}.yaml")
    obs = _load_yaml(manifests_root() / "_shared" / "observability.yaml")
    mq = sut.get("mq") or {}
    os_ = sut.get("os") or {}
    required = {
        "mq.version": mq.get("version"),
        "os.box": os_.get("box"),
        "os.box_version": os_.get("box_version"),
    }
    for key, val in required.items():
        if not val:
            raise ValueError(f"manifest {setup}/{name}: missing required key {key}")
    return Manifest(
        setup=setup,
        name=name,
        mq_version=mq["version"],
        box=os_["box"],
        box_version=os_["box_version"],
        observability=dict(obs),
    )


def setup_platforms(setup: str) -> set[str]:
    """Distinct guest platforms in a setup — the MQ tarballs it needs."""
    topo = _topology()
    groups = topo.get("groups") or {}
    platforms = lab_guests()  # name -> platform
    setup_groups = (topo.get("setups") or {}).get(setup, {}).get("groups") or []
    nodes = {n for g in setup_groups for n in (groups.get(g) or [])}
    return {platforms[n] for n in nodes if n in platforms}


def tarball_name(mq_version: str, platform: str) -> str:
    try:
        suffix = _ARCH_SUFFIX[platform]
    except KeyError as exc:
        raise ValueError(f"no MQ tarball arch mapping for platform {platform!r}") from exc
    return f"{mq_version}-IBM-MQ-Advanced-for-Developers-{suffix}.tar.gz"


def vars_overlay(m: Manifest) -> dict[str, str]:
    """The Ansible extra-vars the roles consume, derived from the manifest."""
    o = m.observability
    return {
        "mq_version": m.mq_version,
        "lab_box": m.box,
        "lab_box_version": m.box_version,
        "prometheus_version": o["prometheus"],
        "node_exporter_version": o["node_exporter"],
        "loki_version": o["loki"],
        "alloy_version": o["alloy"],
        "grafana_version": o["grafana"],
        "mq_exporter_ref": o["mq_metric_samples_ref"],
    }
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `uv run pytest tests/test_manifest.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Full validation + commit**

Run: `vrg-container-run -- vrg-validate` (expect green; 100% branch coverage holds).
```bash
vrg-git add src/mqlab/manifest.py src/mqlab/paths.py tests/test_manifest.py
vrg-commit --type feat --scope manifest --message "manifest core: load/validate, overlay, tarball-name, platforms (#266)"
```

---

## Task 2: MQ artifact acquisition (cache → download → verify)

**Files:**
- Create: `src/mqlab/artifact.py`
- Test: `tests/test_artifact.py`

**Interfaces:**
- Consumes: `mqlab.manifest.tarball_name`, `setup_platforms`.
- Produces: `ensure_mq_tarballs(setup: str, mq_version: str, build_mq_dir: Path, *, fetch: Callable[[str, Path], None]) -> list[Path]` — ensures one tarball per distinct platform; `fetch(name, dest)` downloads on cache miss; `.sha256` sibling verified when present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_artifact.py`:
```python
from __future__ import annotations

import hashlib

import pytest

from mqlab import artifact


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_cache_hit_uses_local_copy_and_verifies_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "setup_platforms", lambda s: {"ubuntu2404-arm64"})
    mqdir = tmp_path / "build" / "mq"
    mqdir.mkdir(parents=True)
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    (mqdir / name).write_bytes(b"TARBALL")
    (mqdir / f"{name}.sha256").write_text(_sha(mqdir / name) + f"  {name}\n")

    calls = []
    out = artifact.ensure_mq_tarballs(
        "distributed-pcmk-ubuntu", "9.4.5.0", mqdir, fetch=lambda n, d: calls.append(n)
    )
    assert calls == []  # cache hit: no download
    assert out == [mqdir / name]


def test_cache_miss_downloads_then_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "setup_platforms", lambda s: {"ubuntu2404-arm64"})
    mqdir = tmp_path / "build" / "mq"
    mqdir.mkdir(parents=True)
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"

    def fake_fetch(n, dest):
        dest.write_bytes(b"DOWNLOADED")

    out = artifact.ensure_mq_tarballs(
        "distributed-pcmk-ubuntu", "9.4.5.0", mqdir, fetch=fake_fetch
    )
    assert (mqdir / name).read_bytes() == b"DOWNLOADED"
    assert out == [mqdir / name]


def test_sha_mismatch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "setup_platforms", lambda s: {"ubuntu2404-arm64"})
    mqdir = tmp_path / "build" / "mq"
    mqdir.mkdir(parents=True)
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    (mqdir / name).write_bytes(b"TARBALL")
    (mqdir / f"{name}.sha256").write_text("deadbeef  " + name + "\n")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        artifact.ensure_mq_tarballs(
            "distributed-pcmk-ubuntu", "9.4.5.0", mqdir, fetch=lambda n, d: None
        )


def test_fetch_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "setup_platforms", lambda s: {"ubuntu2404-arm64"})
    mqdir = tmp_path / "build" / "mq"
    mqdir.mkdir(parents=True)

    def boom(n, dest):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        artifact.ensure_mq_tarballs(
            "distributed-pcmk-ubuntu", "9.4.5.0", mqdir, fetch=boom
        )
```

- [ ] **Step 2: Run, verify fail**

Run: `uv run pytest tests/test_artifact.py -q`
Expected: FAIL (`ModuleNotFoundError: mqlab.artifact`).

- [ ] **Step 3: Implement `src/mqlab/artifact.py`**

```python
"""MQ tarball acquisition (#266): cache -> download -> verify.

A pinned (version x arch) tarball is immutable, so it is safely cached. We never
fail just because a tarball is absent — MQ Advanced for Developers is downloadable
for every arch we use; `fetch` populates the cache on a miss. The sibling `.sha256`
(when present) is verified on acquire: integrity, not version-reconciliation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from mqlab.manifest import setup_platforms, tarball_name

if TYPE_CHECKING:
    from collections.abc import Callable


def _verify_sha256(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return  # no checksum to verify against — immutability is the guarantee
    expected = sidecar.read_text().split()[0].strip().lower()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {path.name}: {actual} != {expected}")


def ensure_mq_tarballs(
    setup: str,
    mq_version: str,
    build_mq_dir: Path,
    *,
    fetch: Callable[[str, Path], None],
) -> list[Path]:
    """Ensure the MQ tarball for each distinct platform in `setup` is present + valid."""
    paths: list[Path] = []
    for platform in sorted(setup_platforms(setup)):
        name = tarball_name(mq_version, platform)
        dest = build_mq_dir / name
        if not dest.exists():
            fetch(name, dest)  # raises on failure (no silent fallback)
        _verify_sha256(dest)
        paths.append(dest)
    return paths
```

(The real `fetch` impl — the IBM dev-edition download URL — is wired at the `cli.py` call site in Task 7; here it is injected so the logic is unit-tested without the network.)

- [ ] **Step 4: Run, verify pass**

Run: `uv run pytest tests/test_artifact.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/artifact.py tests/test_artifact.py
vrg-commit --type feat --scope manifest --message "MQ artifact acquisition: cache/download/verify (#266)"
```

---

## Task 3: Selection persistence (record at create, read downstream, error on mismatch)

**Files:**
- Modify: `src/mqlab/manifest.py` (add the three functions below)
- Test: `tests/test_manifest.py` (extend)

**Interfaces:**
- Produces:
  - `record_selection(setup: str, name: str) -> None` — writes `build/manifests/<setup>.yaml` (the resolved selection).
  - `read_selection(setup: str) -> str | None` — the pinned name, or `None` if not recorded.
  - `resolve_selection(setup: str, requested: str | None) -> str` — at create: pins `requested or "default"`. Downstream (`requested is None`): returns the pinned name. A `requested` that conflicts with the pin raises.

- [ ] **Step 1: Write failing tests** — append to `tests/test_manifest.py`:
```python
def test_resolve_selection_pins_then_reads_back(manifests, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "selection_state_path", lambda s: tmp_path / "state" / f"{s}.yaml")
    # create: pin default
    assert m.resolve_selection("distributed-pcmk-ubuntu", None) == "default"
    assert m.read_selection("distributed-pcmk-ubuntu") == "default"
    # downstream: no flag -> reads the pin
    assert m.resolve_selection("distributed-pcmk-ubuntu", None) == "default"


def test_resolve_selection_conflict_raises(manifests, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "selection_state_path", lambda s: tmp_path / "state" / f"{s}.yaml")
    m.resolve_selection("distributed-pcmk-ubuntu", "repro-945")
    with pytest.raises(ValueError, match="created against 'repro-945'"):
        m.resolve_selection("distributed-pcmk-ubuntu", "default")
```

- [ ] **Step 2: Run, verify fail** — `uv run pytest tests/test_manifest.py -q` → FAIL (`resolve_selection` undefined).

- [ ] **Step 3: Implement** — add to `src/mqlab/manifest.py` (import `selection_state_path` from `mqlab.paths`):
```python
from mqlab.paths import manifests_root, repo_root, selection_state_path  # replace the existing paths import


def record_selection(setup: str, name: str) -> None:
    path = selection_state_path(setup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"setup": setup, "manifest": name}))


def read_selection(setup: str) -> str | None:
    path = selection_state_path(setup)
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text())["manifest"]


def resolve_selection(setup: str, requested: str | None) -> str:
    """Pin/return the selected manifest name. Conflicting mid-lifecycle change raises."""
    pinned = read_selection(setup)
    if requested is None:
        return pinned or "default"
    if pinned is not None and pinned != requested:
        raise ValueError(
            f"lab {setup} was created against '{pinned}'; "
            f"destroy + recreate to switch to '{requested}'"
        )
    record_selection(setup, requested)
    return requested
```
(Note: at `create`, the caller passes `requested = name or "default"` so the pin is always written; see Task 7.)

- [ ] **Step 4: Run, verify pass** — `uv run pytest tests/test_manifest.py -q` → PASS (8 tests).

- [ ] **Step 5: Commit**
```bash
vrg-git add src/mqlab/manifest.py tests/test_manifest.py
vrg-commit --type feat --scope manifest --message "manifest selection persistence (#266)"
```

---

## Task 4: The manifest data files

**Files:**
- Create: `manifests/_shared/observability.yaml`
- Create: `manifests/distributed-pcmk-ubuntu/default.yaml`
- Create: `manifests/distributed-rdqm-rhel/default.yaml`

**Interfaces:** Consumes nothing; the values come from today's role defaults so behavior is unchanged.

- [ ] **Step 1: Read today's pinned obs versions** (so the manifest matches reality)

Run: `grep -rE "loki_version|alloy_version|grafana" ansible/roles/loki/defaults/main.yml ansible/roles/alloy/defaults/main.yml ansible/roles/grafana/tasks/main.yml`
Record the current `loki_version`, `alloy_version`, and the grafana version currently installed (`apt-cache policy grafana` on the obs box, or pin the latest known-good). Use those exact values below in place of the examples.

- [ ] **Step 2: Create the shared obs manifest** — `manifests/_shared/observability.yaml`:
```yaml
# Shared obs stack — singleton served by `mqlab obs up` for every arm (#266).
prometheus: "2.53.2"        # ansible/roles/prometheus/defaults/main.yml
node_exporter: "1.8.2"      # ansible/roles/node-exporter/defaults/main.yml
loki: "<from loki/defaults>"
alloy: "<from alloy/defaults>"
grafana: "<current grafana version>"   # was apt-float; now pinned (Task 5)
mq_metric_samples_ref: "<release tag>" # was 'master' (retires the #172 TODO)
```

- [ ] **Step 3: Create the per-setup SUT manifests**

`manifests/distributed-pcmk-ubuntu/default.yaml`:
```yaml
# Per-setup SUT (MQ / HA / OS). HA is derived (Ubuntu apt floats) → discovered.
mq:
  version: "9.4.5.0"
os:
  box: cloud-image/ubuntu-24.04
  box_version: "20260518.0.0"   # the box version currently in use
```
`manifests/distributed-rdqm-rhel/default.yaml`:
```yaml
# RHEL HA stack ships inside MQ → fixed by mq.version → discovered, not pinned.
mq:
  version: "9.4.5.0"
os:
  box: rhel/9.6-x86_64
  box_version: "<current rhel box version>"
```

- [ ] **Step 4: Validate** — `vrg-container-run -- vrg-validate` (yamllint passes).

- [ ] **Step 5: Commit**
```bash
vrg-git add manifests/
vrg-commit --type feat --scope manifest --message "default manifests: per-setup SUT + shared obs (#266)"
```

---

## Task 5: Role parameterization (MQ tarball, grafana pin) + var defaults

**Files:**
- Create: `ansible/group_vars/all/versions.yml`
- Modify: `ansible/roles/mq-install/tasks/main.yml`, `ansible/roles/mq-client/tasks/main.yml`, `ansible/roles/rdqm-install/tasks/main.yml`
- Modify: `ansible/roles/grafana/tasks/main.yml`

**Interfaces:** Consumes `mq_version`, `grafana_version` (from the overlay or the defaults below).

- [ ] **Step 1: Default vars (preserve current behavior absent a manifest)**

Create `ansible/group_vars/all/versions.yml`:
```yaml
---
# Defaults for manifest-driven versions (#266). The mqlab overlay overrides these;
# absent a manifest, these reproduce today's hardcoded behavior.
mq_version: "9.4.5.0"
grafana_version: ""   # "" = let apt pick (current float); a value pins it (Task 5 step 3)
```
(`prometheus_version`/`node_exporter_version`/`loki_version`/`alloy_version`/`mq_exporter_ref` already exist as role defaults — the overlay overrides them; no new defaults needed.)

- [ ] **Step 2: Template the MQ tarball name** (arch suffix stays role-intrinsic)

In `ansible/roles/mq-install/tasks/main.yml` and `ansible/roles/mq-client/tasks/main.yml`, replace the hardcoded `src:`:
```yaml
    src: "{{ playbook_dir }}/../build/mq/{{ mq_version }}-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
```
In `ansible/roles/rdqm-install/tasks/main.yml`:
```yaml
    src: "{{ playbook_dir }}/../build/mq/{{ mq_version }}-IBM-MQ-Advanced-for-Developers-LinuxX64.tar.gz"
```

- [ ] **Step 3: Pin grafana when a version is given**

In `ansible/roles/grafana/tasks/main.yml`, change the `apt: name: grafana` install task to honor a pin:
```yaml
- name: install grafana (pinned when grafana_version is set)
  ansible.builtin.apt:
    name: "{{ 'grafana=' + grafana_version if grafana_version else 'grafana' }}"
    update_cache: true
  become: true
```

- [ ] **Step 4: Functional gate** (human-run, lab): with `mq_version: "9.4.5.0"` the existing tarball still resolves and `distributed-pcmk-ubuntu` provisions one-pass; with `grafana_version` set, `mqlab obs up` installs that grafana version (`dpkg -l grafana`). `vrg-validate` green for the YAML.

- [ ] **Step 5: Commit**
```bash
vrg-git add ansible/group_vars/all/versions.yml ansible/roles/mq-install ansible/roles/mq-client ansible/roles/rdqm-install ansible/roles/grafana
vrg-commit --type feat --scope manifest --message "parameterize MQ tarball + grafana version; version defaults (#266)"
```

---

## Task 6: Vagrantfile box_version pin

**Files:**
- Modify: `lab/Vagrantfile`

**Interfaces:** Consumes `build/box-versions.json` (`{platform: box_version}`), written by `mqlab` at create (Task 7). Absent the file, boxes float (current behavior).

- [ ] **Step 1: Read the override + set box_version**

In `lab/Vagrantfile`, after `boxes = topology.fetch("boxes")`, load the override and apply it where the box is set:
```ruby
require "json"
bvf = File.join(__dir__, "..", "build", "box-versions.json")
box_versions = File.exist?(bvf) ? JSON.parse(File.read(bvf)) : {}
```
Then where `node.vm.box = platform.fetch("box")` is set, add:
```ruby
      bv = box_versions[spec.fetch("platform", defaults["platform"])]
      node.vm.box_version = bv if bv
```

- [ ] **Step 2: Functional gate** (human-run): with no `build/box-versions.json`, `vagrant up` behaves as today (floats); with `{"ubuntu2404-arm64": "20260518.0.0"}`, `vagrant up` selects that box version (`vagrant box list`). `vrg-validate` green.

- [ ] **Step 3: Commit**
```bash
vrg-git add lab/Vagrantfile
vrg-commit --type feat --scope manifest --message "Vagrantfile: pin box_version from build/box-versions.json (#266)"
```

---

## Task 7: mqlab wiring — `--manifest`, persist, acquire, overlay

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: extend `tests/test_cli_vm.py` for the pure helper (`_apply_manifest`).

**Interfaces:**
- Consumes: `mqlab.manifest` (`load_manifest`, `vars_overlay`, `resolve_selection`, `setup_platforms`), `mqlab.artifact.ensure_mq_tarballs`, `mqlab.paths`.
- Produces: a module helper `_apply_manifest(setup: str, requested: str | None, *, at_create: bool) -> Path` that resolves the selection, (at create) acquires tarballs + writes `build/box-versions.json` + the overlay file, and returns the overlay path; and the `-e @<overlay>` argument added to each `ansible-playbook` `Command`.

- [ ] **Step 1: Add `--manifest` options + the helper**

Add a `--manifest` Typer option (default `None`) to the `vm create`, `vm provision`, `qm create`, and `obs up` commands. Add the helper near the other private helpers in `cli.py`:
```python
import json

from mqlab.artifact import ensure_mq_tarballs
from mqlab.manifest import load_manifest, resolve_selection, vars_overlay
from mqlab.paths import repo_root


def _overlay_path(setup: str):
    p = repo_root() / "build" / "manifests" / f"{setup}.overlay.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _apply_manifest(setup: str, requested: str | None, *, at_create: bool):
    """Resolve the manifest selection and render the Ansible overlay; at create also
    acquire the MQ tarball(s) and pin box_version. Returns the overlay file path."""
    # At create, always pin (default if no flag). Downstream, pass the flag through:
    # None reads the pin, a conflicting value raises (see resolve_selection).
    name = resolve_selection(setup, requested or "default") if at_create else resolve_selection(setup, requested)
    man = load_manifest(setup, name)
    overlay = vars_overlay(man)
    op = _overlay_path(setup)
    op.write_text(json.dumps(overlay))
    if at_create:
        bdir = repo_root() / "build" / "mq"
        bdir.mkdir(parents=True, exist_ok=True)
        ensure_mq_tarballs(setup, man.mq_version, bdir, fetch=_fetch_mq_tarball)
        bvf = repo_root() / "build" / "box-versions.json"
        bvf.write_text(json.dumps({man.box: man.box_version}))  # keyed by platform via box; see note
    return op


def _fetch_mq_tarball(name: str, dest) -> None:  # pragma: no cover - network
    # Real impl: download MQ Advanced for Developers (free) to `dest`. Raise on failure.
    raise RuntimeError(f"MQ tarball {name} absent and auto-download not yet configured; place it at {dest}")
```

> **Note on box_version keying:** `build/box-versions.json` is keyed by **platform** (per Task 6), but a manifest names `os.box` (the box name) + `box_version`. Map box→platform via the topology `boxes` block when writing the file (the platform whose `box` equals `man.box`). Keep the write in one place here.

> **Selection-at-create:** at create, `_apply_manifest(..., at_create=True)` calls `resolve_selection(setup, requested or "default")` so the pin is always written; downstream commands call `_apply_manifest(setup, requested, at_create=False)`, passing `requested` (usually `None`) straight through to read the pin.

- [ ] **Step 2: Inject the overlay into each `ansible-playbook` step**

At each `ansible-playbook` `Command(...)` in the create/provision/qm/obs flows, compute the overlay via `_apply_manifest(...)` and extend the argv:
```python
        op = _apply_manifest(setup_name, manifest, at_create=False)  # at_create=True in the create flow
        Command(
            ["ansible-playbook", Path(setup.provision).name, "-e", f"@{op}"],  # noqa: S607
            cwd=repo_root() / "ansible",
            env=secret_env or None,
        ),
```
For `obs up` (shared obs manifest), render the overlay from `manifests/_shared/observability.yaml` and pass it to `site-obs.yml` the same way (a small `_obs_overlay()` mirroring `_apply_manifest` but reading only the shared obs file).

- [ ] **Step 3: Unit-test the pure helper** — in `tests/test_cli_vm.py`, test that `_apply_manifest` writes the overlay JSON with the expected keys and that a conflicting `--manifest` mid-lifecycle raises (monkeypatch `manifest.selection_state_path` and `_fetch_mq_tarball`). Run `uv run pytest tests/test_cli_vm.py -q` → PASS.

- [ ] **Step 4: Functional gate** (human-run): `mqlab vm create distributed-pcmk-ubuntu` (default manifest) → pins selection, overlay written; `mqlab vm provision distributed-pcmk-ubuntu` consumes the overlay; a second command with a conflicting `--manifest` errors. `vrg-validate` green.

- [ ] **Step 5: Commit**
```bash
vrg-git add src/mqlab/cli.py tests/test_cli_vm.py
vrg-commit --type feat --scope manifest --message "mqlab: thread manifest selection/overlay/acquire through create+provision+obs (#266)"
```

---

## Task 8: Topology-aware version gather play

**Files:**
- Create: `ansible/gather-versions.yml`

**Interfaces:** Produces `build/versions.json` (`{component: version}`) for `runreport` (Task 9). Each component is probed only on the group it lives on (no expected-absence failures); a genuine probe failure fails the play (no silent `''`).

- [ ] **Step 1: Write the gather play**

`ansible/gather-versions.yml` — one play per role-group; each host stashes a `gathered` dict, and a final localhost play merges them (builtin `combine` in a loop — no custom filter) and writes `build/versions.json`. `mqlab` runs this **limited to the active setup's groups**, so only running hosts are targeted (no unreachable-host noise) and absence-by-design is never probed. Any genuine probe failure fails the play (no silent `''`).
```yaml
---
- hosts: pcmk_a:rdqm_a            # QM + HA nodes (ubuntu apt OR rhel bundled — same probes)
  gather_facts: false
  tasks:
    - { name: mq,        ansible.builtin.command: dspmqver -b -f 2,     register: p_mq,   changed_when: false }
    - { name: pacemaker, ansible.builtin.command: pacemakerd --version, register: p_pcmk, changed_when: false }
    - { name: corosync,  ansible.builtin.command: corosync -v,          register: p_coro, changed_when: false }
    - { name: drbd,      ansible.builtin.command: drbdadm --version,     register: p_drbd, changed_when: false }
    - { name: kernel,    ansible.builtin.command: uname -r,             register: p_kern, changed_when: false }
    - name: os
      ansible.builtin.shell: '. /etc/os-release && echo "$NAME $VERSION_ID"'
      register: p_os
      changed_when: false
    - name: stash
      ansible.builtin.set_fact:
        gathered:
          mq: "{{ p_mq.stdout }}"
          pacemaker: "{{ p_pcmk.stdout_lines[0] }}"
          corosync: "{{ p_coro.stdout_lines[0] }}"
          drbd: "{{ p_drbd.stdout_lines[0] }}"
          kernel: "{{ p_kern.stdout }}"
          os: "{{ p_os.stdout }}"

- hosts: svc                     # counterparty QM host (no pacemaker)
  gather_facts: false
  tasks:
    - { name: mq,     ansible.builtin.command: dspmqver -b -f 2, register: d_mq,   changed_when: false }
    - { name: kernel, ansible.builtin.command: uname -r,        register: d_kern, changed_when: false }
    - name: stash
      ansible.builtin.set_fact:
        gathered: { mq: "{{ d_mq.stdout }}", kernel: "{{ d_kern.stdout }}" }

- hosts: obs_box                  # grafana/prometheus/loki/alloy
  gather_facts: false
  tasks:
    - { name: grafana,    ansible.builtin.command: grafana-server -v,                   register: o_graf, changed_when: false }
    - { name: prometheus, ansible.builtin.command: prometheus --version, register: o_prom, changed_when: false }
    - { name: loki,       ansible.builtin.command: loki --version,       register: o_loki, changed_when: false }
    - { name: alloy,      ansible.builtin.command: alloy --version,      register: o_allo, changed_when: false }
    - name: stash
      ansible.builtin.set_fact:
        gathered:
          grafana: "{{ o_graf.stdout_lines[0] }}"
          prometheus: "{{ (o_prom.stdout + o_prom.stderr).split('\n')[0] }}"
          loki: "{{ (o_loki.stdout + o_loki.stderr).split('\n')[0] }}"
          alloy: "{{ (o_allo.stdout + o_allo.stderr).split('\n')[0] }}"

- hosts: probe                    # node_exporter + mq-metric-samples exporter
  gather_facts: false
  tasks:
    - { name: node_exporter, ansible.builtin.command: node_exporter --version, register: pr_ne, changed_when: false }
    - name: mq_metric_samples build (binary + git sha)
      ansible.builtin.shell: 'mq_prometheus --version 2>&1 | head -1; git -C /usr/local/src/mq-metric-samples rev-parse --short HEAD'
      register: pr_mqms
      changed_when: false
    - name: stash
      ansible.builtin.set_fact:
        gathered:
          node_exporter: "{{ (pr_ne.stdout + pr_ne.stderr).split('\n')[0] }}"
          mq_metric_samples: "{{ pr_mqms.stdout_lines | join(' ') }}"

- hosts: localhost
  gather_facts: false
  tasks:
    - name: merge gathered facts from every probed host
      ansible.builtin.set_fact:
        all_versions: "{{ all_versions | default({}) | combine(hostvars[item].gathered | default({})) }}"
      loop: "{{ groups['all'] | difference(['localhost']) }}"
    - name: write build/versions.json
      ansible.builtin.copy:
        dest: "{{ playbook_dir }}/../build/versions.json"
        content: "{{ all_versions | to_nice_json }}\n"
        mode: "0644"
```
Notes for the functional gate: confirm each binary is on `PATH` for the probe (the obs roles unpack to a versioned dir — adjust the command path if `--version` isn't found); several `--version` tools print to **stderr** (handled above by concatenating `stdout + stderr`); `mqlab` invokes this with `ansible-playbook gather-versions.yml --limit <active-setup-groups>` so empty/other-arm groups are simply not run.

- [ ] **Step 2: Functional gate** (human-run): `ansible-playbook gather-versions.yml` against a running setup writes `build/versions.json` with MQ/HA/kernel/obs versions; a down host fails loud (not a silent blank). `vrg-validate` green.

- [ ] **Step 3: Commit**
```bash
vrg-git add ansible/gather-versions.yml
vrg-commit --type feat --scope manifest --message "topology-aware version gather play (#266)"
```

---

## Task 9: runreport — discover the real stack + manifest identity

**Files:**
- Modify: `src/mqlab/runreport.py`
- Test: extend `tests/test_runreport.py`

**Interfaces:**
- `RunMetadata` gains `manifest: str = ""`.
- `capture_metadata(..., manifest_reader: Callable[[], str])` — assembles `manifest` like the other readers.
- `read_versions()` reads `build/versions.json` (merged into the existing vagrant/ansible dict).

- [ ] **Step 1: Write the failing test** — append to `tests/test_runreport.py`:
```python
def test_capture_metadata_includes_manifest() -> None:
    from mqlab.runreport import capture_metadata

    md = capture_metadata(
        "distributed-pcmk-ubuntu",
        "20260618T000000Z",
        commit_reader=lambda: "abc",
        digest_reader=lambda: "def",
        version_reader=lambda: {"mq": "9.4.5.0"},
        manifest_reader=lambda: "distributed-pcmk-ubuntu/default",
    )
    assert md.manifest == "distributed-pcmk-ubuntu/default"
    assert md.versions == {"mq": "9.4.5.0"}


def test_config_digest_is_manifest_sensitive(tmp_path) -> None:
    from mqlab.runreport import read_config_digest

    man = tmp_path / "sel.yaml"
    man.write_text("manifest: default\n")
    before = read_config_digest([man])
    man.write_text("manifest: repro-945\n")
    assert read_config_digest([man]) != before  # digest tracks the pinned manifest
```

- [ ] **Step 2: Run, verify fail** — `uv run pytest tests/test_runreport.py -q` → FAIL (`manifest_reader` / `manifest` unknown; the digest test passes once the path is wired in step 4).

- [ ] **Step 3: Implement** — in `src/mqlab/runreport.py`:

Add the field:
```python
@dataclass(frozen=True)
class RunMetadata:
    setup: str
    commit: str
    timestamp: str
    config_digest: str
    versions: dict[str, str] = field(default_factory=dict)
    manifest: str = ""
```
Thread the reader:
```python
def capture_metadata(
    setup: str,
    timestamp: str,
    *,
    commit_reader: Callable[[], str],
    digest_reader: Callable[[], str],
    version_reader: Callable[[], dict[str, str]],
    manifest_reader: Callable[[], str],
) -> RunMetadata:
    return RunMetadata(
        setup=setup,
        commit=commit_reader(),
        timestamp=timestamp,
        config_digest=digest_reader(),
        versions=version_reader(),
        manifest=manifest_reader(),
    )
```
Add the manifest line to `to_markdown()` after `Config digest`:
```python
            f"- Manifest: `{m.manifest or '(none)'}`",
```
Extend `read_versions()` to merge the gathered JSON:
```python
def read_versions() -> dict[str, str]:  # pragma: no cover - shells out / reads build
    def _v(argv: list[str]) -> str:
        return subprocess.check_output(argv, text=True).strip().splitlines()[0]  # noqa: S603

    out = {"vagrant": _v(["vagrant", "--version"]), "ansible": _v(["ansible", "--version"])}  # noqa: S607
    gathered = repo_root() / "build" / "versions.json"
    if gathered.exists():
        out.update(json.loads(gathered.read_text()))
    return out
```
(Add `from mqlab.paths import repo_root` and ensure `json` is imported.)

Update the existing `capture_metadata(...)` call site at `cli.py:1087` to pass `manifest_reader=` (real impl reads the pinned selection via `mqlab.manifest.read_selection`, formatted as `<setup>/<name>`).

**Bind the manifest into `config_digest` (§5.4 / §7).** That call site's `digest_reader` calls `read_config_digest([...])` at `cli.py:1091`. Add the pinned manifest path to the hashed list so the report's `config_digest` changes whenever the manifest changes:
```python
from mqlab.paths import selection_state_path  # add to cli.py imports

        digest_reader=lambda: read_config_digest(
            [*existing_config_paths, selection_state_path(setup_name)]
        ),
```
`read_config_digest` hashes file bytes and is already content-sensitive (proven by `test_config_digest_is_manifest_sensitive`), so including the pinned-selection path is the whole binding — the report is now cryptographically tied to the exact pinned stack.

- [ ] **Step 4: Run, verify pass** — `uv run pytest tests/test_runreport.py -q` → PASS (existing + new, incl. the digest-sensitivity test). `vrg-validate` green (100% branch coverage — the new `read_versions` branch is `# pragma: no cover`, consistent with the existing readers).

- [ ] **Step 5: Commit**
```bash
vrg-git add src/mqlab/runreport.py tests/test_runreport.py
vrg-commit --type feat --scope manifest --message "runreport: discover real stack + manifest identity (#266)"
```

---

## Task 10: Functional acceptance gate (lab)

**Files:** none (acceptance).

- [ ] **Step 1: Clean drive-and-discover from the manifest**
```bash
mqlab vm create distributed-pcmk-ubuntu        # pins default, acquires/locates the tarball, writes box-versions
mqlab vm provision distributed-pcmk-ubuntu     # consumes the overlay
mqlab qm create distributed-pcmk-ubuntu
mqlab obs up                                    # shared obs manifest
```
Expected: one-pass; the box, MQ, and obs versions installed match the manifests.

- [ ] **Step 2: Discover matches the manifest**
```bash
ansible-playbook gather-versions.yml           # writes build/versions.json
```
Expected: `build/versions.json` MQ/obs versions equal the manifest pins; HA/kernel/OS are present (discovered).

- [ ] **Step 3: Report carries both coordinates** — a generated run-report bundle's `report.md` shows `Manifest:` (the pinned id) and `Versions:` (the real stack). The asked-for and got coordinates pair up.

- [ ] **Step 4: Record results in the PR** — one-pass bring-up, discovered==pinned for the drivable set, report carries manifest id + full versions.

---

## Self-Review

**Spec coverage:** §3 architecture → Tasks 1,7,9; §4 manifest (two namespaces, selection, persistence, arch) → Tasks 1,3,4,7; §5 drive wiring (overlay, box, MQ acquire, digest) → Tasks 1,2,5,6,7; §5.1 acquisition → Task 2; §5.4 / §7 `config_digest` binding → Task 9 step 3 (with `test_config_digest_is_manifest_sensitive`); §6 discover (topology-aware, full component set) → Task 8; §7 reporting (versions + manifest id) → Task 9; §9 testing → per-task TDD + Task 10 gate; §10/§11 → out of scope / resolved in spec. **Alignment (2026-06-18):** §5.1 MQ-vs-RHEL conflation corrected in the spec; `config_digest` binding promoted from a self-review note to a concrete Task 9 step + test; Task 8 fleshed out to the full §6 component set.

**Placeholder scan:** the `<from …>` / `<current …>` tokens in Task 4 are *data to read from the live roles/box in step 1*, not code placeholders — each step says where to get the exact value. The `_apply_manifest` selection ternary in Task 7 is deliberately flagged for simplification at implementation (the note spells out the rule: `resolve_selection(setup, requested or "default")` at create, `requested` passed through downstream).

**Type consistency:** `Manifest`, `load_manifest`, `vars_overlay`, `setup_platforms`, `tarball_name`, `ensure_mq_tarballs`, `resolve_selection`/`read_selection`/`record_selection`, `capture_metadata(manifest_reader=…)`, `RunMetadata.manifest` are used consistently across Tasks 1–9. Overlay var names (`mq_version`, `grafana_version`, `lab_box_version`, `prometheus_version`, `mq_exporter_ref`) match the role consumers in Tasks 5–6.
