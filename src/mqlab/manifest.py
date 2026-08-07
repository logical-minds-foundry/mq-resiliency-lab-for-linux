"""MQ tarball naming + the shared observability version overlay (#266/#350).

The per-setup SUT version manifest was dropped in the #350 cutover (bootstrap uses
the repo-default MQ version, host-resolved per platform). What survives here is the
arch→tarball-name mapping every fetch path needs, plus the shared observability
manifest overlay the obs provision consumes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from mqlab.hostfacts import AARCH64, X86_64, probe
from mqlab.paths import manifests_root, repo_root
from mqlab.platforms import box_build_arch

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.hostfacts import HostFacts

# The OS-family segment of the MQ-for-Developers tarball name, per VM platform. RHEL
# and AlmaLinux take the generic "Linux" build; Ubuntu takes "UbuntuLinux". The ARCH
# segment is resolved SEPARATELY (below), through the same box_build_arch authority the
# box builder consumes (#103 D10) — so a host-resolved Ubuntu fat box (no `arch:` pin)
# picks up the build host's arch instead of the old baked-in x86 literal, and RHEL
# (x86-pinned) stays LinuxX64 on every host. Membership here is also the known-platform
# gate: an absent platform is an error, not a silent default.
_OS_PREFIX = {
    "ubuntu2404-arm64": "UbuntuLinux",
    "ubuntu2404-x86_64": "UbuntuLinux",
    "rhel96-x86_64": "Linux",
    # The fat RDQM box platform (#604) is RHEL x86_64 — same LinuxX64 tarball as
    # rhel96-x86_64; the bake consumes it, the stack-prereq ensure keeps it cached for
    # the repointed rdqm_a/rdqm_b nodes.
    "mq-rdqm-rhel9": "Linux",
    # The fat native-HA RHEL box platform (#667, epic .github#88) is RHEL x86_64 — same
    # LinuxX64 tarball. The six nha-rhel-* nodes are repointed to it (#668) and run MQ,
    # so _stack_mq_platforms feeds it into tarball_name.
    "mq-nativeha-rhel9": "Linux",
    # The fat obs box platform (#605) is Ubuntu, now host-resolved (#103 D3): it takes
    # the same Ubuntu tarball svc/app/probe already need for this host's arch. The obs
    # node is a commons member, so _commons_mq_platforms feeds this into tarball_name.
    "obs-ubuntu2404": "UbuntuLinux",
    # The fat MQ-commons box platform (#659) is Ubuntu, now host-resolved (#103 D3):
    # svc/app/probe repoint to it and are MQ commons, so _commons_mq_platforms feeds this
    # into tarball_name; the bake consumed the host-arch Ubuntu tarball + the MQ SDK.
    "mq-ubuntu2404": "UbuntuLinux",
    # The fat native-HA Ubuntu box platform (#103 T6) is Ubuntu, host-resolved (no `arch:`
    # pin — the OS-as-only-variable peer of mq-nativeha-rhel9). The six nha-ubuntu-* nodes
    # are repointed to it and run MQ, so _stack_mq_platforms feeds it into tarball_name;
    # the arch resolves to the build host (UbuntuLinuxARM64 on the Mac, X64 on the cloud).
    "mq-nativeha-ubuntu": "UbuntuLinux",
    # The fat pcmk-ubuntu box platform (#103 T7) is Ubuntu, host-resolved (no `arch:` pin).
    # The six Pacemaker cluster nodes (pcmk-a1..3, pcmk-b1..3) are repointed to it and run
    # MQ via roles/mq-install, so _stack_mq_platforms feeds it into tarball_name; the arch
    # resolves to the build host (UbuntuLinuxARM64 on the Mac, X64 on the cloud). The SAN
    # targets carry no MQ payload and are NOT here (host-resolved base box, D8).
    "pcmk-ubuntu": "UbuntuLinux",
    "alma9-x86_64": "Linux",
}

# Canonical arch (hostfacts) -> the arch token in the MQ-for-Developers tarball name.
_MQ_ARCH_TOKEN = {AARCH64: "ARM64", X86_64: "X64"}

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


def _box_registry() -> dict[str, dict[str, Any]]:
    """The `boxes:` registry from lab/topology.yaml (box/platform name -> entry)."""
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    boxes: dict[str, dict[str, Any]] = data.get("boxes", {})
    return boxes


def tarball_name(mq_version: str, platform: str, facts: HostFacts | None = None) -> str:
    """The MQ-for-Developers tarball filename for a platform on this host (#103 D10).

    The OS family is a static per-platform property; the ARCH is resolved through the
    box_build_arch authority against the platform's box-registry entry — so a pinned box
    (RHEL x86_64) keeps its arch and an un-pinned Ubuntu fat box tracks the build host.
    Acquisition and the bake therefore agree by construction. Facts default to probe().
    """
    try:
        prefix = _OS_PREFIX[platform]
    except KeyError as exc:
        raise ValueError(f"no MQ tarball arch mapping for platform {platform!r}") from exc
    facts = facts if facts is not None else probe()
    arch = box_build_arch(_box_registry().get(platform, {}), facts)
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
