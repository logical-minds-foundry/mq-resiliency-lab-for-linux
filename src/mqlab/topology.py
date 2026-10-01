"""The effective lab topology: lab/topology.yaml + the MQLAB_ENV profile (#1202, epic .github#275).

The same stack runs with different staging-lever values per platform — macOS throttled,
cloud maximal — without forking topology. `env_profiles.<env>` in lab/topology.yaml is
deep-merged onto the base topology at load, selected by the ``MQLAB_ENV`` environment
variable — or, when that is unset, by auto-detecting the platform (#1245):

- a known env     -> the base with that profile's overrides applied (explicit always wins);
- anything else   -> ``ValueError`` (fail loud — never a silent default);
- unset or empty  -> the platform is detected (``detect_platform``): Apple Virtualization
  -> ``macos``, Google Compute Engine -> ``cloud``; an inconclusive detection (bare metal,
  another hypervisor) -> the base topology, unchanged, and ``EnvResolution.describe()``
  says so in one line that bootstrap/teardown/commons-up print. Never a silent profile.

Detection reads ``/sys/class/dmi/id/product_name`` (no subprocess) and falls back to
``systemd-detect-virt`` (bare name via PATH, bounded by a timeout). Both are properties of
the host, so every mqlab invocation on a host resolves the same env — bootstrap and
teardown agree, and teardown releases the huge pages bootstrap reserved (#1241).

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
import subprocess
from dataclasses import dataclass
from pathlib import Path
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


# Platform auto-detection (#1245). The DMI strings are the ones systemd-detect-virt itself
# matches (prefix match on product_name, src/basic/virt.c dmi_vendor_table:
# https://github.com/systemd/systemd/blob/b43fed88efe34889a49a7710a92141849dc4906d/src/basic/virt.c#L197-L198)
# and Google's documented GCE check (`dmidecode -s system-product-name` contains
# "Google Compute Engine": https://docs.cloud.google.com/compute/docs/instances/detect-compute-engine).
# Observed on the macOS dev VM: product_name "Apple Virtualization Generic Platform",
# sys_vendor "Apple Inc." (sys_vendor alone would also match real Mac hardware, so it is
# evidence only, never the match key).
DMI_ROOT = Path("/sys/class/dmi/id")
_DMI_PRODUCT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Apple Virtualization", "macos"),
    ("Google Compute Engine", "cloud"),
)
DETECT_VIRT = "systemd-detect-virt"
DETECT_VIRT_TIMEOUT = 5.0
# systemd-detect-virt's ids (virt.c virtualization_table: "apple", "google").
_DETECT_VIRT_IDS = {"apple": "macos", "google": "cloud"}

EXPLICIT = "explicit"
DETECTED = "detected"
INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class EnvResolution:
    """Which env profile applies and why: ``env`` None = the base topology."""

    env: str | None
    source: str  # EXPLICIT | DETECTED | INCONCLUSIVE
    evidence: str

    def describe(self) -> str:
        """The one line bootstrap/teardown/commons-up print and the perf report records."""
        if self.source == EXPLICIT:
            return f"environment: {self.env} (explicit {self.evidence})"
        if self.source == DETECTED:
            return f"environment: {self.env} (detected: {self.evidence})"
        return (
            f"{ENV_VAR} not set and platform not recognised — using base topology ({self.evidence})"
        )


def _read_dmi(root: Path, name: str) -> str | None:
    try:
        return (root / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None  # an absent DMI file (no DMI, or a container) is simply no evidence


def run_detect_virt() -> tuple[str | None, str]:
    """``systemd-detect-virt``'s verdict as (env or None, evidence). Never raises: a
    missing binary, a timeout or an unmapped id is inconclusive, and the evidence says
    which. (It exits 1 when it prints "none" — still a valid, inconclusive answer.)"""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, bare name via PATH by design
            [DETECT_VIRT],
            capture_output=True,
            text=True,
            timeout=DETECT_VIRT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{DETECT_VIRT} unavailable ({type(exc).__name__}: {exc})"
    virt = proc.stdout.strip()
    env = _DETECT_VIRT_IDS.get(virt)
    if env is None:
        return None, f"{DETECT_VIRT}={virt or '<no output>'} (exit {proc.returncode})"
    return env, f"{DETECT_VIRT}={virt}"


def detect_platform(dmi_root: Path | None = None) -> tuple[str | None, str]:
    """The env this host maps to, as (env or None, evidence): DMI product_name first,
    then ``run_detect_virt``. Deterministic per host; never raises."""
    root = DMI_ROOT if dmi_root is None else dmi_root
    product = _read_dmi(root, "product_name")
    if product:
        for prefix, env in _DMI_PRODUCT_PREFIXES:
            if product.startswith(prefix):
                vendor = _read_dmi(root, "sys_vendor")
                return env, f"DMI product_name={product!r}, sys_vendor={vendor!r}"
    virt_env, evidence = run_detect_virt()
    return virt_env, f"DMI product_name={product!r}; {evidence}"


def resolve_env(environ: dict[str, str] | None = None) -> EnvResolution:
    """Explicit ``$MQLAB_ENV`` wins (an unknown value fails loud); unset/empty -> the
    detected platform; inconclusive -> the base topology (env None), never a guess."""
    explicit = (os.environ if environ is None else environ).get(ENV_VAR, "")
    if explicit:
        _check_known(explicit)
        return EnvResolution(explicit, EXPLICIT, f"{ENV_VAR}={explicit}")
    env, evidence = detect_platform()
    if env is None:
        return EnvResolution(None, INCONCLUSIVE, evidence)
    return EnvResolution(env, DETECTED, evidence)


def _check_known(selected: str) -> None:
    if selected not in KNOWN_ENVS:
        msg = f"{ENV_VAR}={selected!r} is not a known environment; use one of {sorted(KNOWN_ENVS)}"
        raise ValueError(msg)


def _read_raw() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def load() -> dict[str, Any]:
    """The effective topology for the resolved env (explicit ``MQLAB_ENV`` or detected)."""
    return effective(_read_raw())


def effective(topo: dict[str, Any], env: str | None = None) -> dict[str, Any]:
    """Return ``topo`` with the selected env profile deep-merged on (input not mutated).

    ``env`` defaults to ``resolve_env()`` (explicit ``$MQLAB_ENV``, else the detected
    platform; "" = the base). The ``env_profiles`` block is validated (keys must be known
    envs) and stripped from the result, so downstream sees plain topology.
    """
    selected = (resolve_env().env or "") if env is None else env
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
    _check_known(selected)
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
