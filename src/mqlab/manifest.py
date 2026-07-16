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
    # The fat native-HA RHEL box platform (#667, epic .github#88) is RHEL x86_64, so it
    # takes the same LinuxX64 tarball as rhel96-x86_64 / mq-rdqm-rhel9. The six nha-rhel-*
    # nodes are repointed to it (#668) and run MQ, so _stack_mq_platforms feeds it into
    # tarball_name; the bake consumed the tarball for the baked base-MQ install and the
    # stack-prereq ensure keeps it cached for the repointed nha_rhel_a/nha_rhel_b nodes.
    "mq-nativeha-rhel9": "LinuxX64",
    # The fat obs box platform (#605) is Ubuntu x86_64, so it takes the same
    # UbuntuLinuxX64 tarball as ubuntu2404-x86_64. The obs node is a commons member,
    # so _commons_mq_platforms feeds this into tarball_name; it resolves to the one
    # Ubuntu tarball svc/app/probe already need (the bake consumed it for the baked
    # mq_prometheus cgo build against the MQ SDK).
    "obs-ubuntu2404": "UbuntuLinuxX64",
    # The fat MQ-commons box platform (#659) is Ubuntu x86_64, so it takes the same
    # UbuntuLinuxX64 tarball as ubuntu2404-x86_64. svc/app/probe are repointed to it and
    # are MQ commons, so _commons_mq_platforms feeds this into tarball_name; the bake
    # consumed the same tarball for the MQ deb install + the mq_prometheus cgo SDK.
    "mq-ubuntu2404": "UbuntuLinuxX64",
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
