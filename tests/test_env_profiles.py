"""MQLAB_ENV per-environment profile overrides over lab/topology.yaml (#1202, epic .github#275).

The same stack runs with different lever values per platform (macOS throttled, cloud
maximal) without forking topology: `env_profiles.<env>` is deep-merged onto the base at
load, selected by MQLAB_ENV. Unset/empty = base unchanged; an unknown env fails loud.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from mqlab import phases, platforms, stacks
from mqlab import topology as t
from mqlab.hostfacts import X86_64, HostFacts
from mqlab.paths import repo_root

X86_KVM = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)

BASE: dict[str, Any] = {
    "boxes": {"ubuntu2404-x86_64": {"box": "cloud-image/ubuntu-24.04", "arch": "x86_64"}},
    "defaults": {"cpus": 1, "memory": 1024},
    "boot_batch": 4,
    "nodes": {
        "obs": {"cpus": 12, "memory": 10240, "nics": {"net-mgmt": "10.50.0.2"}},
        "mon-probe": {"cpus": 1, "memory": 1024},
    },
    "env_profiles": {
        "macos": {"boot_batch": 2, "nodes": {"obs": {"cpus": 8}}},
        "cloud": {},
    },
}


def _real_topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


# --------------------------------------------------------------------------- #
# effective() — the pure deep-merge
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("env", [None, ""])
def test_unset_or_empty_env_leaves_base_unchanged(monkeypatch, env):
    if env is None:
        monkeypatch.delenv(t.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(t.ENV_VAR, env)
    out = t.effective(copy.deepcopy(BASE))
    assert out["boot_batch"] == 4
    assert out["nodes"]["obs"]["cpus"] == 12
    assert "env_profiles" not in out


def test_macos_profile_overrides_boot_batch_and_node_cpus(monkeypatch):
    monkeypatch.setenv(t.ENV_VAR, "macos")
    out = t.effective(copy.deepcopy(BASE))
    assert out["boot_batch"] == 2
    assert out["nodes"]["obs"]["cpus"] == 8
    # deep-merge: untouched sibling keys and nodes survive
    assert out["nodes"]["obs"]["memory"] == 10240
    assert out["nodes"]["obs"]["nics"] == {"net-mgmt": "10.50.0.2"}
    assert out["nodes"]["mon-probe"] == {"cpus": 1, "memory": 1024}


def test_explicit_env_argument_wins_over_environment(monkeypatch):
    monkeypatch.setenv(t.ENV_VAR, "cloud")
    assert t.effective(copy.deepcopy(BASE), env="macos")["boot_batch"] == 2


def test_empty_profile_is_identity(monkeypatch):
    monkeypatch.setenv(t.ENV_VAR, "cloud")
    out = t.effective(copy.deepcopy(BASE))
    assert out["boot_batch"] == 4
    assert out["nodes"]["obs"]["cpus"] == 12


def test_does_not_mutate_input():
    base = copy.deepcopy(BASE)
    t.effective(base, env="macos")
    assert base == BASE


def test_topology_without_env_profiles_block(monkeypatch):
    base = {k: v for k, v in copy.deepcopy(BASE).items() if k != "env_profiles"}
    assert t.effective(base, env="")["boot_batch"] == 4
    with pytest.raises(ValueError, match="no env_profiles.macos"):
        t.effective(base, env="macos")


def test_null_profile_is_identity():
    base = copy.deepcopy(BASE)
    base["env_profiles"]["cloud"] = None
    assert t.effective(base, env="cloud")["boot_batch"] == 4


def test_null_base_node_accepts_cpus_override():
    base = copy.deepcopy(BASE)
    base["nodes"]["mon-probe"] = None
    base["env_profiles"]["macos"] = {"nodes": {"mon-probe": {"cpus": 3}}}
    assert t.effective(base, env="macos")["nodes"]["mon-probe"] == {"cpus": 3}


def test_bogus_env_fails_loud(monkeypatch):
    monkeypatch.setenv(t.ENV_VAR, "bogus")
    with pytest.raises(ValueError, match="MQLAB_ENV='bogus'"):
        t.effective(copy.deepcopy(BASE))


def test_known_env_missing_from_topology_fails_loud():
    base = copy.deepcopy(BASE)
    del base["env_profiles"]["cloud"]
    with pytest.raises(ValueError, match="no env_profiles.cloud"):
        t.effective(base, env="cloud")


def test_unknown_profile_key_in_topology_fails_loud():
    base = copy.deepcopy(BASE)
    base["env_profiles"]["linux"] = {}
    with pytest.raises(ValueError, match="unknown env_profiles key"):
        t.effective(base, env="")


def test_env_profiles_must_be_a_mapping():
    base = copy.deepcopy(BASE)
    base["env_profiles"] = ["macos"]
    with pytest.raises(ValueError, match="env_profiles must be a mapping"):
        t.effective(base, env="")


@pytest.mark.parametrize(
    ("profile", "match"),
    [
        ({"memory": 1}, "may not override 'memory'"),
        ({"nodes": {"ghost": {"cpus": 2}}}, "unknown node 'ghost'"),
        ({"nodes": {"obs": {"memory": 2048}}}, "may not override nodes.obs.memory"),
        ("fast", "must be a mapping"),
        ({"nodes": ["obs"]}, "nodes must be a mapping"),
        ({"nodes": {"obs": 8}}, "nodes.obs must be a mapping"),
    ],
)
def test_malformed_or_out_of_scope_profile_fails_loud(profile, match):
    base = copy.deepcopy(BASE)
    base["env_profiles"]["macos"] = profile
    with pytest.raises(ValueError, match=match):
        t.effective(base, env="macos")


# --------------------------------------------------------------------------- #
# Downstream consumers see the effective topology
# --------------------------------------------------------------------------- #
@pytest.fixture
def macos_profile_topology(monkeypatch):
    """The real topology with a macos profile of `{boot_batch: 2, nodes: {obs: {cpus: 8}}}`
    injected, served to every loader that reads lab/topology.yaml."""
    raw = _real_topology()
    raw["env_profiles"] = {"macos": {"boot_batch": 2, "nodes": {"obs": {"cpus": 8}}}, "cloud": {}}
    monkeypatch.setattr(t, "_read_raw", lambda: copy.deepcopy(raw))
    return raw


def test_phases_boot_batch_follows_env(monkeypatch, macos_profile_topology):
    monkeypatch.delenv(t.ENV_VAR, raising=False)
    assert phases._boot_batch() == macos_profile_topology["boot_batch"]
    monkeypatch.setenv(t.ENV_VAR, "macos")
    assert phases._boot_batch() == 2


def test_resolved_render_node_cpus_follow_env(monkeypatch, tmp_path, macos_profile_topology):
    """The Vagrantfile consumes build/work/lab/topology.resolved.yaml (rendered by
    platforms.ensure_resolved), so the override must land in that rendered file."""
    monkeypatch.setattr(platforms, "resolved_topology_path", lambda: tmp_path / "r.yaml")
    monkeypatch.delenv(t.ENV_VAR, raising=False)
    base_cpus = macos_profile_topology["nodes"]["obs"]["cpus"]
    rendered = yaml.safe_load(platforms.ensure_resolved(facts=X86_KVM).read_text())
    assert rendered["nodes"]["obs"]["cpus"] == base_cpus
    monkeypatch.setenv(t.ENV_VAR, "macos")
    rendered = yaml.safe_load(platforms.ensure_resolved(facts=X86_KVM).read_text())
    assert rendered["nodes"]["obs"]["cpus"] == 8


def test_stacks_load_effective_topology(monkeypatch, macos_profile_topology):
    monkeypatch.setenv(t.ENV_VAR, "macos")
    assert stacks._topology()["boot_batch"] == 2


def test_load_reads_real_topology(monkeypatch):
    monkeypatch.delenv(t.ENV_VAR, raising=False)
    assert t.load()["nodes"] == _real_topology()["nodes"]


# --------------------------------------------------------------------------- #
# Guardrails on the real lab/topology.yaml
# --------------------------------------------------------------------------- #
def test_real_env_profiles_keys_are_known_envs():
    profiles = _real_topology().get("env_profiles") or {}
    assert set(profiles) <= {"macos", "cloud"}
    assert frozenset({"macos", "cloud"}) == t.KNOWN_ENVS


@pytest.mark.parametrize("env", sorted(t.KNOWN_ENVS))
def test_real_topology_applies_every_profile(env):
    out = t.effective(_real_topology(), env=env)
    assert "boot_batch" in out
