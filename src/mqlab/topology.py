"""The effective lab topology: lab/topology.yaml + the MQLAB_ENV profile (#1202, epic .github#275).

The same stack runs with different staging-lever values per platform — macOS throttled,
cloud maximal — without forking topology. `env_profiles.<env>` in lab/topology.yaml is
deep-merged onto the base topology at load, selected by the ``MQLAB_ENV`` environment
variable:

- unset or empty  -> the base topology, unchanged;
- a known env     -> the base with that profile's overrides applied;
- anything else   -> ``ValueError`` (fail loud — never a silent default).

The override surface is deliberately narrow — only the levers the epic spec names
(``boot_batch``, per-node ``cpus``, and ``memory_backing`` — #1241's huge-page-backed guest
RAM, proven by spike #1240) — and a profile touching anything else, or a node the base
does not declare, fails loud rather than silently inventing topology. Widen
``_TOP_LEVEL_LEVERS`` / ``_NODE_LEVERS`` when a new lever is proven by evidence. A lever
with a closed value set (``memory_backing``) is also value-checked, so a typo fails at
load instead of reaching the Vagrantfile.

Consumers of the levers read through ``load()`` (phases' boot batch, the resolved-topology
render the Vagrantfile consumes, the Stack registry), so they all see one effective view.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import yaml

from mqlab.paths import repo_root

ENV_VAR = "MQLAB_ENV"
KNOWN_ENVS = frozenset({"macos", "cloud"})

_PROFILES_KEY = "env_profiles"
# memory_backing (#1241): how guest RAM is backed. "hugepages" = 2 MiB huge pages (the
# vagrant-libvirt `memorybacking :hugepages` domain element) — the macOS/arm64 nested-virt
# fix from spike #1240. Absent = the default 4 KiB backing. No other value is accepted.
MEMORY_BACKING = "memory_backing"
HUGEPAGES = "hugepages"
MEMORY_BACKING_VALUES = frozenset({HUGEPAGES})

_TOP_LEVEL_LEVERS = frozenset({"boot_batch", MEMORY_BACKING})
_NODE_LEVERS = frozenset({"cpus"})
# Levers whose value must come from a closed set — validated on a profile override AND
# by memory_backing() wherever a consumer reads it.
_LEVER_VALUES: dict[str, frozenset[str]] = {MEMORY_BACKING: MEMORY_BACKING_VALUES}


def _read_raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def load() -> dict[str, Any]:
    """The effective topology for the current ``MQLAB_ENV``."""
    return effective(_read_raw())


def effective(topo: dict[str, Any], env: str | None = None) -> dict[str, Any]:
    """Return ``topo`` with the selected env profile deep-merged on (input not mutated).

    ``env`` defaults to ``$MQLAB_ENV``. The ``env_profiles`` block is validated (keys
    must be known envs) and stripped from the result, so downstream sees plain topology.
    """
    selected = os.environ.get(ENV_VAR, "") if env is None else env
    out = copy.deepcopy(topo)
    profiles = out.pop(_PROFILES_KEY, None) or {}
    if not isinstance(profiles, dict):
        msg = f"topology {_PROFILES_KEY} must be a mapping, got {profiles!r}"
        raise ValueError(msg)
    unknown = sorted(set(profiles) - KNOWN_ENVS)
    if unknown:
        msg = f"unknown {_PROFILES_KEY} key(s) {unknown}; allowed: {sorted(KNOWN_ENVS)}"
        raise ValueError(msg)
    if not selected:
        return out
    if selected not in KNOWN_ENVS:
        msg = f"{ENV_VAR}={selected!r} is not a known environment; use one of {sorted(KNOWN_ENVS)}"
        raise ValueError(msg)
    if selected not in profiles:
        msg = f"{ENV_VAR}={selected!r} but lab/topology.yaml has no {_PROFILES_KEY}.{selected}"
        raise ValueError(msg)
    _apply(out, profiles[selected] or {}, selected)
    return out


def _apply(topo: dict[str, Any], profile: Any, env: str) -> None:
    where = f"{_PROFILES_KEY}.{env}"
    if not isinstance(profile, dict):
        msg = f"{where} must be a mapping, got {profile!r}"
        raise ValueError(msg)
    for key, value in profile.items():
        if key == "nodes":
            _apply_nodes(topo.get("nodes") or {}, value, where)
        elif key in _TOP_LEVEL_LEVERS:
            _check_value(key, value, where)
            topo[key] = copy.deepcopy(value)
        else:
            msg = f"{where} may not override {key!r}; levers: {sorted(_TOP_LEVEL_LEVERS)} + nodes"
            raise ValueError(msg)


def _check_value(key: str, value: Any, where: str) -> None:
    allowed = _LEVER_VALUES.get(key)
    if allowed is not None and value not in allowed:
        msg = f"{where}.{key} must be one of {sorted(allowed)}, got {value!r}"
        raise ValueError(msg)


def memory_backing(topo: dict[str, Any]) -> str | None:
    """The effective ``memory_backing`` lever (#1241): ``"hugepages"`` or None (default).

    Validated here too (not only on a profile override), so a bad value set directly in
    the base topology fails loud for every consumer rather than reaching the Vagrantfile.
    """
    value = topo.get(MEMORY_BACKING)
    if value is None:
        return None
    _check_value(MEMORY_BACKING, value, "topology")
    return str(value)


def _apply_nodes(nodes: dict[str, Any], overrides: Any, where: str) -> None:
    if not isinstance(overrides, dict):
        msg = f"{where}.nodes must be a mapping, got {overrides!r}"
        raise ValueError(msg)
    for name, node_over in overrides.items():
        if name not in nodes:
            msg = f"{where}.nodes: unknown node {name!r} (not declared in the base topology)"
            raise ValueError(msg)
        if not isinstance(node_over, dict):
            msg = f"{where}.nodes.{name} must be a mapping, got {node_over!r}"
            raise ValueError(msg)
        for key, value in node_over.items():
            if key not in _NODE_LEVERS:
                msg = f"{where} may not override nodes.{name}.{key}; levers: {sorted(_NODE_LEVERS)}"
                raise ValueError(msg)
            nodes[name] = {**(nodes[name] or {}), key: copy.deepcopy(value)}
