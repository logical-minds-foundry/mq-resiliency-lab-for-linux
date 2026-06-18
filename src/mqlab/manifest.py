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
