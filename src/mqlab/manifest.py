"""Version manifest — turn a (setup, name) into validated drive inputs (#266).

The per-setup SUT manifest (MQ/HA/OS) is merged with the shared obs manifest
(`manifests/_shared/observability.yaml`). Drivable-only: every key maps to a value
we can actually set; derived versions (RHEL HA bundled in MQ, drbd kmod) are
discovered, never pinned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import yaml

from mqlab.fleet import lab_guests
from mqlab.paths import manifests_root, repo_root, selection_state_path

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.hostfacts import HostFacts

# Arch suffix in the MQ-for-Developers tarball name, per VM platform. The Ubuntu
# platform a node uses is host-resolved (#276), so both arches map here.
_ARCH_SUFFIX = {
    "ubuntu2404-arm64": "UbuntuLinuxARM64",
    "ubuntu2404-x86_64": "UbuntuLinuxX64",
    "rhel96-x86_64": "LinuxX64",
    "alma9-x86_64": "LinuxX64",
}

# Canonical MQ-for-Developers version, used to ensure a setup's tarball(s) when the
# setup has no manifest pinning one (e.g. monitoring). Manifested setups override it
# with their own `mq.version` pin. Mirrors scripts/fetch-mq.sh's VER. (#333)
DEFAULT_MQ_VERSION = "9.4.5.0"


@dataclass(frozen=True)
class Manifest:
    setup: str
    name: str
    mq_version: str
    box: str
    box_version: str
    observability: dict[str, str]


def _topology() -> dict[str, Any]:
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return cast("dict[str, Any]", data)


def _load_yaml(path: Path) -> dict[str, Any]:
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


def manifest_exists(setup: str, name: str = "default") -> bool:
    """Whether a setup has a manifest — lets callers stay backward-compatible (#266)."""
    return (manifests_root() / setup / f"{name}.yaml").exists()


def setup_platforms(setup: str, facts: HostFacts | None = None) -> set[str]:
    """Distinct guest platforms in a setup — the MQ tarballs it needs. The Ubuntu
    platform is host-resolved via lab_guests(facts) (native-preferred, #276)."""
    topo = _topology()
    groups = topo.get("groups") or {}
    platforms = lab_guests(facts)  # name -> platform
    setup_groups = (topo.get("setups") or {}).get(setup, {}).get("groups") or []
    nodes = {n for g in setup_groups for n in (groups.get(g) or [])}
    return {platforms[n] for n in nodes if n in platforms}


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


def vars_overlay(m: Manifest) -> dict[str, str]:
    """The Ansible extra-vars the roles consume, derived from the manifest."""
    return {
        "mq_version": m.mq_version,
        "lab_box": m.box,
        "lab_box_version": m.box_version,
        **_obs_vars(m.observability),
    }


def obs_overlay() -> dict[str, str]:
    """The obs version vars from the shared obs manifest, for `mqlab obs up` (#266)."""
    return _obs_vars(_load_yaml(manifests_root() / "_shared" / "observability.yaml"))


def box_version_pins(m: Manifest) -> dict[str, str]:
    """platform -> box_version for every topology platform using this manifest's box.

    Keyed by platform because the Vagrantfile reads build/work/box-versions.json that way.
    """
    boxes = _topology().get("boxes") or {}
    return {p: m.box_version for p, cfg in boxes.items() if (cfg or {}).get("box") == m.box}


def record_selection(setup: str, name: str) -> None:
    path = selection_state_path(setup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"setup": setup, "manifest": name}))


def read_selection(setup: str) -> str | None:
    path = selection_state_path(setup)
    if not path.exists():
        return None
    return str(yaml.safe_load(path.read_text())["manifest"])


def resolve_selection(setup: str, requested: str | None) -> str:
    """Pin/return the selected manifest name. A conflicting mid-lifecycle change raises."""
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
