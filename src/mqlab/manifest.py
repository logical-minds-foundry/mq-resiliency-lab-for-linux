"""MQ tarball naming + the shared observability version overlay (#266/#350).

The per-setup SUT version manifest was dropped in the #350 cutover (bootstrap uses
the repo-default MQ version, host-resolved per platform). What survives here is the
arch→tarball-name mapping every fetch path needs, plus the shared observability
manifest overlay the obs provision consumes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import manifests_root

if TYPE_CHECKING:
    from pathlib import Path

# Arch suffix in the MQ-for-Developers tarball name, per VM platform. The Ubuntu
# platform a node uses is host-resolved (#276), so both arches map here.
_ARCH_SUFFIX = {
    "ubuntu2404-arm64": "UbuntuLinuxARM64",
    "ubuntu2404-x86_64": "UbuntuLinuxX64",
    "rhel96-x86_64": "LinuxX64",
    # The fat RDQM box platform (#604) is RHEL x86_64, so it takes the same LinuxX64
    # tarball as rhel96-x86_64 — the bake (build-fatbox.sh) consumes it, and the
    # stack-prereq ensure keeps it cached for the repointed rdqm_a/rdqm_b nodes.
    "mq-rdqm-rhel9": "LinuxX64",
    "alma9-x86_64": "LinuxX64",
}

# Canonical MQ-for-Developers version. The #350 stack/commons bootstrap ensures a
# platform's tarball at this version. Mirrors scripts/fetch-mq.sh's VER. (#333)
DEFAULT_MQ_VERSION = "9.4.5.0"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} is not a mapping")
    return data


def tarball_name(mq_version: str, platform: str) -> str:
    try:
        suffix = _ARCH_SUFFIX[platform]
    except KeyError as exc:
        raise ValueError(f"no MQ tarball arch mapping for platform {platform!r}") from exc
    return f"{mq_version}-IBM-MQ-Advanced-for-Developers-{suffix}.tar.gz"


_OBS_VAR_MAP = {
    "prometheus_version": "prometheus",
    "node_exporter_version": "node_exporter",
    "loki_version": "loki",
    "alloy_version": "alloy",
    "grafana_version": "grafana",
    "mq_exporter_ref": "mq_metric_samples_ref",
}


def _obs_vars(obs: dict[str, str]) -> dict[str, str]:
    return {var: obs[key] for var, key in _OBS_VAR_MAP.items()}


def obs_overlay() -> dict[str, str]:
    """The obs version vars from the shared obs manifest, for the obs provision (#266)."""
    return _obs_vars(_load_yaml(manifests_root() / "_shared" / "observability.yaml"))
