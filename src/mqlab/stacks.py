"""Canonical stack registry (#350 Task 1a) — the 4-stack authoritative model.

A Stack is the union of what previously lived in two places:
- an arm entry (mechanism, cluster_group, short, verbs)
- a distributed setup (full-HADR groups, qm VIPs, alloc)

QM names derive exclusively from the stack's `short` token (#351): `<short>APP`
is the HA/app QM, `<short>SVC` the counterparty. No retired QM literal appears
here — names live in `short` only.

Added alongside the existing `setups:`/`arms:` surface in Task 1a. Task 1b does
the cutover (migrate callers, retire the old blocks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from mqlab.paths import repo_root


@dataclass(frozen=True)
class QmConfig:
    """A stack's queue-manager identity (#109): the QM name, its internal data-plane
    VIP, its partner-facing (net-ext) VIP for the inter-business link (#146), and —
    when this QM talks to a counterparty — that counterparty's CONNAME (#147).

    `vip_ext` is optional: the Pacemaker stack binds it as a second VIP on the QM
    resource group, but RDQM allows only one floating IP per QM (#216 spike), so the
    RDQM stack omits it and the partner reaches the QM by per-node CONNAME list.

    `vip` is optional too: Native HA (#246) has no floating IP at all — clients
    reach the active instance via a multi-instance CONNAME list — so its stacks
    omit `vip` entirely."""

    name: str
    vip: str = ""
    vip_ext: str = ""
    svc_conn: str | None = None
    svc: str = ""

    @property
    def qm_app(self) -> str:
        return self.name

    @property
    def qm_svc(self) -> str:
        return self.svc

    @property
    def chl_to_svc(self) -> str:
        return f"{self.name}.{self.svc}"

    @property
    def chl_to_app(self) -> str:
        return f"{self.svc}.{self.name}"


@dataclass(frozen=True)
class Stack:
    """A canonical lab stack — the full HADR shape for one mechanism+OS combination.

    Fields:
        name:          stack key as it appears in topology.yaml's stacks: block.
        mechanism:     HA/DR mechanism string (e.g. "pacemaker-san", "rdqm", "native-ha").
        os:            base OS ("ubuntu" or "rhel").
        short:         4-char uppercase token; QM names derive from this (#351).
        verbs:         per-verb dispatch dict (raw from YAML; commands/playbooks).
        cluster_group: Ansible group name for the cluster nodes (e.g. "pcmk_a", "rdqm_a").
                       This is the correct probe target for qm-status — NOT groups[0], which
                       may be a SAN host with no Pacemaker/MQ tooling. None for reserved stacks.
        groups:        ordered list of atomic group names (site-A + site-B + SANs).
        qm:            QmConfig — names derived from short, VIPs/svc_conn from topology.
        provision:     path to the top-level Ansible playbook, or None (reserved stacks).
        secrets:       list of Vault/secret names required to provision this stack.
        alloc:         allocation constants dict (exporter ports, app_unit, svc_port).
    """

    name: str
    mechanism: str
    os: str
    short: str
    verbs: dict[str, Any]
    cluster_group: str | None
    groups: list[str]
    qm: QmConfig
    provision: str | None
    secrets: list[str]
    alloc: dict[str, Any]


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def _qm_from_stack(short: str, qm_cfg: dict[str, Any]) -> QmConfig:
    """Build a QmConfig from a stack entry's short token and its qm: sub-block.

    Names derive purely from short — <short>APP for the HA/app QM, <short>SVC for
    the counterparty. No retired-name fallback: all stacks are required to declare
    a short.
    """
    return QmConfig(
        name=f"{short}APP",
        vip=qm_cfg.get("vip", ""),
        vip_ext=qm_cfg.get("vip_ext", ""),
        svc_conn=qm_cfg.get("svc_conn"),
        svc=f"{short}SVC",
    )


def lab_stacks() -> dict[str, Stack]:
    """All canonical stacks from topology.yaml's stacks: block, keyed by name."""
    data = _topology()
    result: dict[str, Stack] = {}
    for name, cfg in (data.get("stacks") or {}).items():
        cfg = cfg or {}
        short = cfg["short"]
        result[name] = Stack(
            name=name,
            mechanism=cfg["mechanism"],
            os=cfg["os"],
            short=short,
            verbs=dict(cfg.get("verbs") or {}),
            cluster_group=cfg.get("cluster_group") or None,
            groups=list(cfg.get("groups") or []),
            qm=_qm_from_stack(short, dict(cfg.get("qm") or {})),
            provision=cfg.get("provision"),
            secrets=list(cfg.get("secrets") or []),
            alloc=dict(cfg.get("alloc") or {}),
        )
    return result


def stack_members(name: str) -> list[str] | None:
    """Hosts belonging to a stack's groups (flattened in declared order, de-duped).

    Returns None if the stack name is not found. An empty list is returned for
    reserved stacks (e.g. nativeha-ubuntu) that declare groups: [].

    Reads the topology's `stacks`/`groups` blocks directly (a pure membership
    query) rather than constructing a full Stack, so a guest-selection caller does
    not depend on every stack field being present.
    """
    data = _topology()
    stacks = data.get("stacks") or {}
    if name not in stacks:
        return None
    all_groups: dict[str, list[str]] = {
        g: list(hosts) for g, hosts in (data.get("groups") or {}).items()
    }
    members: list[str] = []
    for g in (stacks[name] or {}).get("groups", []):
        for host in all_groups.get(g, []):
            if host not in members:
                members.append(host)
    return members
