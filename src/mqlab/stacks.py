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
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.hostfacts import AARCH64
from mqlab.paths import repo_root

if TYPE_CHECKING:
    from mqlab.hostfacts import HostFacts

# Display labels for a stack's Grafana dashboard folder (#59). Derived from
# mechanism+os so a new stack needs no separate folder literal — the folder title
# is "<mechanism label> (<OS label>)", e.g. "Native HA (RHEL)".
_MECH_LABEL = {
    "pacemaker-san": "PCMK",
    "rdqm": "RDQM",
    "native-ha": "Native HA",
}
_OS_LABEL = {"ubuntu": "Ubuntu", "rhel": "RHEL"}


def dashboard_folder_for(mechanism: str, os: str) -> str:
    """The Grafana dashboard folder label for a HA mechanism + OS (#59): "<Mechanism>
    (<OS>)", e.g. "Native HA (RHEL)". Fail loud on an unlabelled mechanism/os rather than
    silently mis-foldering a board."""
    try:
        mech = _MECH_LABEL[mechanism]
    except KeyError as exc:
        raise ValueError(f"no dashboard-folder label for mechanism {mechanism!r}") from exc
    try:
        os_label = _OS_LABEL[os]
    except KeyError as exc:
        raise ValueError(f"no dashboard-folder label for os {os!r}") from exc
    return f"{mech} ({os_label})"


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
    short: str = ""
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

    @property
    def req_queue(self) -> str:
        """This stack's own request queue on the shared SVCQM — the per-stack
        independence axis of #446 (each stack owns {SHORT}.SVC.REQUEST)."""
        return f"{self.short}.SVC.REQUEST" if self.short else ""


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

    @property
    def dashboard_folder(self) -> str:
        """The Grafana folder this stack's dashboards live under (#59), e.g.
        "Native HA (RHEL)" — derived from mechanism+os (see dashboard_folder_for)."""
        return dashboard_folder_for(self.mechanism, self.os)


def rhel_stack_unsupported_reason(stack: Stack, facts: HostFacts) -> str | None:
    """Why this stack cannot run on this host, or None when it can (#847).

    The RHEL stacks (``os == "rhel"``: rdqm-rhel, nativeha-rhel) require an
    x86_64 host. RHEL is not — and is not expected to become — available for
    Apple Silicon, and emulated cross-arch box builds are disabled by design
    (#103 D11), so on an aarch64 host only the Ubuntu stacks are supported
    (Ubuntu-for-ARM + emulated MQ). Every other combination is supported.

    Pure and display-safe — never raises, reads only the stack's declared OS and
    the host arch — so both the bring-up preflight gate and `mqlab doctor` can
    consult it. Follows the #350 stack registry's ``os`` field as the authority on
    which stacks are RHEL-based, rather than re-deriving arch from the box layer.
    """
    if stack.os == "rhel" and facts.arch == AARCH64:
        return (
            f"the {stack.name!r} stack requires an x86_64 host: the RHEL arms are "
            f"unsupported on aarch64 (this host). RHEL is not available for Apple "
            f"Silicon and emulated cross-arch box builds are disabled (#103 D11). "
            f"On aarch64 only the Ubuntu stacks are supported (Ubuntu-for-ARM + "
            f"emulated MQ)."
        )
    return None


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def _svc_identity(topo: dict[str, Any]) -> tuple[str, str]:
    """The shared Business-B counterparty identity from the top-level `svc:` block:
    (SVC QM name, its CONNAME). One SVCQM serves every stack (#446); its name derives
    from the block's short (<short>QM) so no QM literal is hardcoded. Fail loud — a
    topology that declares stacks must declare a valid svc block."""
    svc = topo.get("svc") or {}
    short = svc.get("short")
    conn = svc.get("conn")
    if not short or not conn:
        raise ValueError("topology `svc:` block must declare `short` and `conn` (#446)")
    return f"{short}QM", str(conn)


def _qm_from_stack(short: str, qm_cfg: dict[str, Any], svc_name: str, svc_conn: str) -> QmConfig:
    """Build a QmConfig from a stack entry's short token, its qm: sub-block, and the
    SHARED svc identity. The app QM name derives from short (<short>APP); the
    counterparty is the single SVCQM (name + conn threaded in), not a per-stack
    {short}SVC (#446). No retired-name fallback: all stacks declare a short."""
    return QmConfig(
        name=f"{short}APP",
        short=short,
        vip=qm_cfg.get("vip", ""),
        vip_ext=qm_cfg.get("vip_ext", ""),
        svc_conn=svc_conn,
        svc=svc_name,
    )


def lab_stacks() -> dict[str, Stack]:
    """All canonical stacks from topology.yaml's stacks: block, keyed by name."""
    data = _topology()
    stacks_cfg = data.get("stacks") or {}
    # A topology that declares stacks needs the shared counterparty; a stackless one
    # (e.g. a pki/empty fixture) does not — the svc block is required only when used.
    svc_name, svc_conn = _svc_identity(data) if stacks_cfg else ("", "")
    result: dict[str, Stack] = {}
    for name, cfg in stacks_cfg.items():
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
            qm=_qm_from_stack(short, dict(cfg.get("qm") or {}), svc_name, svc_conn),
            provision=cfg.get("provision"),
            secrets=list(cfg.get("secrets") or []),
            alloc=dict(cfg.get("alloc") or {}),
        )
    return result


# Topology group-name prefix marking a SAN target group (san_a, san_b). The SAN
# targets are the only stack members that stay on the host-resolved base box and run
# the drbd-san / iscsi-target install-half, so their debs are pre-cached (#796).
_SAN_GROUP_PREFIX = "san_"


def stack_san_targets(name: str) -> list[str]:
    """SAN target hosts of a stack — members of its `san_*` groups (san-a/san-b).

    Returns [] for a stack with no SAN targets (rdqm / native-ha) or an unknown
    stack name. Gates the SAN-deb pre-cache: only a stack that actually has SAN
    targets needs the install-half debs staged (#796). Reads topology directly, the
    same pure-membership way `stack_members` does.
    """
    data = _topology()
    stacks = data.get("stacks") or {}
    all_groups: dict[str, list[str]] = {
        g: list(hosts) for g, hosts in (data.get("groups") or {}).items()
    }
    targets: list[str] = []
    for g in (stacks.get(name) or {}).get("groups", []):
        if not g.startswith(_SAN_GROUP_PREFIX):
            continue
        for host in all_groups.get(g, []):
            if host not in targets:
                targets.append(host)
    return targets


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
