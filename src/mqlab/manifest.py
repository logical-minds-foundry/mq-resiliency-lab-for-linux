"""MQ tarball naming + the shared observability version overlay (#266/#350).

The per-setup SUT version manifest was dropped in the #350 cutover (bootstrap uses
the repo-default MQ version, host-resolved per box). What survives here is the
arch→tarball-name mapping every fetch path needs, plus the shared observability
manifest overlay the obs provision consumes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.hostfacts import AARCH64, X86_64, probe
from mqlab.paths import manifests_root, mq_version_pin_path
from mqlab.platforms import box_build_arch

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.hostfacts import HostFacts
    from mqlab.versions import BoxEntry

# The OS-family segment of the MQ-for-Developers tarball name, per OS family (epic
# .github#280). RHEL takes the generic "Linux" build; Ubuntu takes "UbuntuLinux". The
# ARCH segment is resolved SEPARATELY (below), through the same box_build_arch authority
# the box builder consumes (#103 D10) — so a host-resolved Ubuntu box (no arch pin)
# picks up the build host's arch, and RHEL (x86-pinned by the catalog) stays LinuxX64 on
# every host. Membership here is also the known-family gate: an absent family is an
# error, not a silent default.
_FAMILY_PREFIX = {"ubuntu": "UbuntuLinux", "rhel": "Linux"}

# Canonical arch (hostfacts) -> the arch token in the MQ-for-Developers tarball name.
_MQ_ARCH_TOKEN = {AARCH64: "ARM64", X86_64: "X64"}

_FOUR_PART_VERSION = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _read_mq_version_pin() -> str:
    """Read the single authoritative MQ-version pin (``lab/mq-version``, #1071).

    Returns the bare 4-part version string every MQ consumer resolves to. A missing
    or malformed pin is an error — never a silent default: a wrong MQ version must
    fail loudly at import, not fall back to a stale literal.
    """
    path = mq_version_pin_path()
    text = path.read_text().strip()
    if not _FOUR_PART_VERSION.match(text):
        raise ValueError(f"MQ version pin {path} is not a bare 4-part version: {text!r}")
    return text


# Canonical MQ-for-Developers version, read from the single authoritative pin
# (lab/mq-version, #1071). The #350 stack/commons bootstrap ensures a platform's
# tarball at this version; scripts/fetch-mq.sh resolves the same pin. cli.py imports
# this symbol — keep it exported. (#333)
DEFAULT_MQ_VERSION = _read_mq_version_pin()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} is not a mapping")
    return data


def tarball_name(mq_version: str, entry: BoxEntry, facts: HostFacts | None = None) -> str:
    """The MQ-for-Developers tarball filename for a box on this host (#103 D10).

    The OS family comes from the box's catalog OS entry; the ARCH is resolved through
    the box_build_arch authority against that entry's arch pin — so a pinned box (RHEL
    x86_64) keeps its arch and an un-pinned Ubuntu box tracks the build host.
    Acquisition and the bake therefore agree by construction. Facts default to probe().
    """
    family = entry.os.ref.family
    try:
        prefix = _FAMILY_PREFIX[family]
    except KeyError as exc:
        raise ValueError(f"no MQ tarball mapping for OS family {family!r}") from exc
    facts = facts if facts is not None else probe()
    arch = box_build_arch({"arch": entry.os.arch_pin}, facts)
    return f"{mq_version}-IBM-MQ-Advanced-for-Developers-{prefix}{_MQ_ARCH_TOKEN[arch]}.tar.gz"


_OBS_VAR_MAP = {
    "prometheus_version": "prometheus",
    "node_exporter_version": "node_exporter",
    "loki_version": "loki",
    "alloy_version": "alloy",
    "grafana_version": "grafana",
    "mq_exporter_ref": "mq_metric_samples_ref",
    # logsearch tier (#829, epic .github#149): OpenSearch + Dashboards share one upstream
    # version, pinned once in the shared manifest so a re-bake never changes the engine
    # version under an existing snapshot. The opensearch/opensearch-dashboards roles that
    # consume these are added in later #149 tasks.
    "opensearch_version": "opensearch",
    "opensearch_dashboards_version": "opensearch_dashboards",
    # Data Prepper — the Alloy -> OTLP -> OpenSearch connector (#939). Pinned in the same
    # shared manifest so a re-bake never changes the shipper version; the data-prepper role
    # draws its version from here rather than hardcoding.
    "data_prepper_version": "data_prepper",
}


def _obs_vars(obs: dict[str, str]) -> dict[str, str]:
    return {var: obs[key] for var, key in _OBS_VAR_MAP.items()}


def obs_overlay() -> dict[str, str]:
    """The obs version vars from the shared obs manifest, for the obs provision (#266)."""
    return _obs_vars(_load_yaml(manifests_root() / "_shared" / "observability.yaml"))
