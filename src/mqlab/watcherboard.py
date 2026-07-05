"""The Watcher (#488) — the lab-state board (lab-watcher). A pure builder: topology
dict in → Grafana dashboard dict out, no I/O beyond reading the real topology in the
`lab_watcher_dashboard()` entry (exactly like clusterboard.lab_cluster_dashboard).

Two object-driven sections, top-down, sharing an aligned column grid so the values
read as columns (the dashboard.py convention — every support host and every stack
renders a row at all times; a missing signal is a coloured absence, never a vanished
row):

  ① Support layer — one instrument-strip row per commons support host (DNS/obs/probe/
    svc/app): status stripe · role · defining-service pill · CPU · mem · the role's one
    domain metric · uptime. Support hosts come from the commons groups; the per-role
    metadata (role label, defining-service unit, domain metric) is keyed by GROUP NAME
    (infra/obs_box/probe/svc/app), never by a host literal.

  ② Stacks — one rollup row per stack from the topology stack registry: status stripe ·
    mechanism · Site A (live) · Site B (DR) · flow · drill ↗ to the stack's cockpit. QM
    owner + node health come from the same cluster_* metrics the cockpits emit; the SAN
    groups fold into the pcmk row's site node sets. QM names derive from each stack's
    #351 short — no QM literal is hardcoded.

Design note: the sections read as an aligned "instrument strip" via stat tiles pinned to
a shared column grid (the closest existing model is dashboard.py's one-row-per-object
layout). A single Grafana Table cannot carry the per-row-heterogeneous domain metric
(DNS q/s vs scrape N/M vs queue depth …) nor the per-stack drill links, so the strip is
built from aligned stat tiles rather than one Table panel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.clusterboard import _STALE_MAP, _row_header, _stat
from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_UID = "lab-watcher"  # pinned — referenced by mqlab obs open + docs

_GREEN, _RED = "green", "red"

# 1 → green up, 0 → red down, no-data → STALE (fail-loud: a scrape target that is down
# still has an `up` series reading 0, so the row shows DOWN rather than vanishing).
_UP_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "0": {"color": _RED, "text": "down", "index": 0},
            "1": {"color": _GREEN, "text": "up", "index": 1},
        },
    },
    _STALE_MAP,
]
# Defining-service pill: all watched units active → green running; any inactive → red
# down; no systemd data → STALE (coloured absence, not a false green).
_SERVICE_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "0": {"color": _RED, "text": "down", "index": 0},
            "1": {"color": _GREEN, "text": "running", "index": 1},
        },
    },
    _STALE_MAP,
]


@dataclass(frozen=True)
class RoleSpec:
    """Per-support-role metadata, keyed by the commons GROUP name (not a host literal):
    the human role label, the systemd unit regex the defining-service pill watches, and
    the role's one domain metric (title · PromQL template · Grafana unit). The domain
    template is `.format(host=…, svc=…)`-resolved so a template may scope to the host or
    reference the shared SVC QM name; templates that use neither are unaffected."""

    role: str
    unit: str
    domain_title: str
    domain: str
    domain_unit: str


# The curated support-host order: infra (the DNS pair) first, then obs, probe, svc, app.
# Each entry is a commons GROUP; its hosts expand from topology `groups` (infra → two).
_SUPPORT_GROUPS = ["infra", "obs_box", "probe", "svc", "app"]

_ROLE_SPEC: dict[str, RoleSpec] = {
    # DNS q/s has no exporter yet (the systemd collector only proves `named` is running);
    # the tile reads object-driven "No data" until a BIND query exporter emits this — the
    # design's stated treatment of a not-yet-wired signal (a visible coloured absence).
    "infra": RoleSpec(
        role="DNS",
        unit="named.service|bind9.service",
        domain_title="⟲ q/s",
        domain='sum(rate(lab_dns_queries_total{{host="{host}"}}[1m]))',
        domain_unit="reqps",
    ),
    "obs_box": RoleSpec(
        role="Observability",
        unit="prometheus.service|grafana-server.service",
        domain_title="scrape ✓",
        domain="count(up == 1)",
        domain_unit="short",
    ),
    "probe": RoleSpec(
        role="MQ probe",
        unit="mq_prometheus.*.service|mq-.*.service",
        domain_title="exporters",
        domain='count(up{{job="ibmmq"}} == 1)',
        domain_unit="short",
    ),
    "svc": RoleSpec(
        role="SVC counterparty",
        unit="mq_prometheus.*.service|mq-.*.service",
        domain_title="SVCQM depth",
        domain='max(ibmmq_queue_depth{{qmgr="{svc}"}})',
        domain_unit="short",
    ),
    "app": RoleSpec(
        role="App client",
        unit="mq-app-requester.*.service",
        domain_title="round-trip",
        domain="sum(rate(app_roundtrip_total[1m]))",
        domain_unit="reqps",
    ),
}

# Each stack's existing cockpit board (the drill ↗ target), keyed by stack name.
_COCKPIT_UID: dict[str, str] = {
    "pcmk-ubuntu": "lab-pcmk-cluster",
    "rdqm-rhel": "lab-rdqm-cluster",
    "nativeha-rhel": "lab-nativeha-cluster",
    "nativeha-ubuntu": "lab-nativeha-ubuntu-cluster",
}

# The support-row column grid (x, w) — one shared layout so values pin into aligned
# columns and read down the board. Sums to the 24-col Grafana grid.
_SUPPORT_COLS = {
    "stripe": (0, 2),
    "role": (2, 4),
    "service": (6, 3),
    "cpu": (9, 3),
    "mem": (12, 3),
    "domain": (15, 5),
    "uptime": (20, 4),
}
# The stack-row column grid.
_STACK_COLS = {
    "stripe": (0, 2),
    "mech": (2, 3),
    "site_a": (5, 4),
    "n_a": (9, 2),
    "site_b": (11, 4),
    "n_b": (15, 2),
    "flow": (17, 4),
    "drill": (21, 3),
}

_ROW_H = 3


def _bg(panel: dict[str, Any]) -> dict[str, Any]:
    """Turn a stat into a background-lit stripe/chip (no sparkline) — the status-stripe
    and value-only tiles read as solid colour blocks, like clusterboard's site badges."""
    panel["options"]["colorMode"] = "background"
    panel["options"]["graphMode"] = "none"
    return panel


def _text(content: str, x: int, y: int, w: int, h: int = _ROW_H) -> dict[str, Any]:
    """A small markdown text tile (a static label cell — role, mechanism)."""
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def _title_banner(y: int) -> dict[str, Any]:
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {
            "mode": "markdown",
            "content": "## The Watcher · lab support layer + stack rollup",
        },
    }


def _support_row(group: str, host: str, svc_qm: str, y: int) -> list[dict[str, Any]]:
    """One support host's instrument strip: status stripe · role · defining-service pill ·
    CPU · mem · the role's domain metric · uptime — all scoped to this host, aligned to the
    shared support column grid."""
    spec = _ROLE_SPEC[group]
    ds = "prometheus"
    sx, sw = _SUPPORT_COLS["stripe"]
    stripe = _bg(
        _stat(
            host,
            f'up{{job="node",host="{host}"}}',
            ds,
            sx,
            y,
            mappings=_UP_MAP,
            w=sw,
            h=_ROW_H,
        )
    )
    rx, rw = _SUPPORT_COLS["role"]
    role = _text(f"**{host}**<br/>{spec.role}", rx, y, rw)
    vx, vw = _SUPPORT_COLS["service"]
    service = _stat(
        "service",
        f'min(node_systemd_unit_state{{host="{host}",name=~"{spec.unit}",state="active"}})',
        ds,
        vx,
        y,
        mappings=_SERVICE_MAP,
        w=vw,
        h=_ROW_H,
    )
    cx, cw = _SUPPORT_COLS["cpu"]
    cpu = _stat(
        "CPU",
        f'100 - (avg by (host)(rate(node_cpu_seconds_total{{host="{host}",mode="idle"}}[1m]))'
        " * 100)",
        ds,
        cx,
        y,
        unit="percent",
        w=cw,
        h=_ROW_H,
    )
    mx, mw = _SUPPORT_COLS["mem"]
    mem = _stat(
        "mem",
        f'100 * (1 - node_memory_MemAvailable_bytes{{host="{host}"}}'
        f' / node_memory_MemTotal_bytes{{host="{host}"}})',
        ds,
        mx,
        y,
        unit="percent",
        w=mw,
        h=_ROW_H,
    )
    dx, dw = _SUPPORT_COLS["domain"]
    domain = _stat(
        spec.domain_title,
        spec.domain.format(host=host, svc=svc_qm),
        ds,
        dx,
        y,
        unit=spec.domain_unit,
        w=dw,
        h=_ROW_H,
    )
    ux, uw = _SUPPORT_COLS["uptime"]
    uptime = _stat(
        "uptime",
        f'node_time_seconds{{host="{host}"}} - node_boot_time_seconds{{host="{host}"}}',
        ds,
        ux,
        y,
        unit="dtdurations",
        w=uw,
        h=_ROW_H,
    )
    return [stripe, role, service, cpu, mem, domain, uptime]


def _support_rows(topo: dict[str, Any], y: int = 0) -> list[dict[str, Any]]:
    """Every commons support host's instrument-strip row, in the curated order, starting
    at `y`. Hosts expand from the topology `groups`; an absent/empty support group yields
    no rows (object-driven — the rows that exist are exactly the hosts the topology has)."""
    groups = topo.get("groups", {})
    svc_qm = f"{topo.get('svc', {}).get('short', '')}QM"
    panels: list[dict[str, Any]] = []
    cursor = y
    for group in _SUPPORT_GROUPS:
        for host in groups.get(group, []):
            panels.extend(_support_row(group, host, svc_qm, cursor))
            cursor += _ROW_H
    return panels


def _owner_resource(mechanism: str, short: str) -> str:
    """The `cluster_resource_owner` resource label for a stack. Pacemaker owns the QM via
    the pacemaker resource id (mq_qm); the other mechanisms own by the short-derived QM
    name (#351)."""
    if mechanism == "pacemaker-san":
        return "mq_qm"
    return f"{short}APP"


def _sites(groups: list[str]) -> tuple[list[str], list[str]]:
    """Split a stack's groups into (site-A, site-B) by the `_a`/`_b` suffix — the SAN
    groups (san_a/san_b) fold into their site's node set automatically."""
    a = [g for g in groups if g.endswith("_a")]
    b = [g for g in groups if g.endswith("_b")]
    return a, b


def _site_tiles(
    label: str, resource: str, groups: list[str], cols: tuple[str, str], y: int
) -> list[dict[str, Any]]:
    """A site's two aligned cells: the active-QM holder (name of the host owning the QM on
    this site, or No data when this side is the standby) + the site's online-node count."""
    ds = "prometheus"
    sel = "|".join(groups)
    owner_col, n_col = cols
    ox, ow = _STACK_COLS[owner_col]
    owner = _stat(
        label,
        f'max by (holder)(cluster_resource_owner{{resource="{resource}",groups=~"{sel}"}})',
        ds,
        ox,
        y,
        mappings=[_STALE_MAP],
        text_mode="name",
        w=ow,
        h=_ROW_H,
    )
    nx, nw = _STACK_COLS[n_col]
    count = _stat(
        "n",
        f'sum(cluster_node_online{{groups=~"{sel}"}})',
        ds,
        nx,
        y,
        w=nw,
        h=_ROW_H,
    )
    return [owner, count]


def _stack_row(name: str, cfg: dict[str, Any], y: int) -> list[dict[str, Any]]:
    """One stack's rollup row: status stripe (worst-of the stack's node health) · mechanism
    · Site A (live) · Site B (DR) · flow (round-trip rate) · drill ↗ to the cockpit uid."""
    ds = "prometheus"
    mechanism = cfg["mechanism"]
    short = cfg["short"]
    groups = cfg.get("groups", [])
    resource = _owner_resource(mechanism, short)
    a_groups, b_groups = _sites(groups)
    all_sel = "|".join(groups)

    sx, sw = _STACK_COLS["stripe"]
    stripe = _bg(
        _stat(
            name,
            f'min(cluster_node_online{{groups=~"{all_sel}"}})',
            ds,
            sx,
            y,
            mappings=_UP_MAP,
            w=sw,
            h=_ROW_H,
        )
    )
    mx, mw = _STACK_COLS["mech"]
    mech = _text(mechanism, mx, y, mw)
    site_a = _site_tiles("Site A · live", resource, a_groups, ("site_a", "n_a"), y)
    site_b = _site_tiles("Site B · DR", resource, b_groups, ("site_b", "n_b"), y)
    fx, fw = _STACK_COLS["flow"]
    flow = _stat(
        "flow",
        "sum(rate(app_roundtrip_total[1m]))",
        ds,
        fx,
        y,
        unit="reqps",
        w=fw,
        h=_ROW_H,
    )
    dx, dw = _STACK_COLS["drill"]
    drill = _stat(
        "drill ↗",
        f'min(cluster_node_online{{groups=~"{all_sel}"}})',
        ds,
        dx,
        y,
        mappings=[_STALE_MAP],
        w=dw,
        h=_ROW_H,
    )
    drill["links"] = [{"title": "Cockpit ↗", "url": f"/d/{_COCKPIT_UID.get(name, '')}"}]
    return [stripe, mech, *site_a, *site_b, flow, drill]


def _stack_rows(topo: dict[str, Any], y: int = 0) -> list[dict[str, Any]]:
    """A rollup row per stack in the topology stack registry, starting at `y`. Stacks are
    read from the passed topology (keeping build_watcher pure); the real-topology entry
    threads the same registry lab_stacks() reads."""
    stacks = topo.get("stacks", {})
    panels: list[dict[str, Any]] = []
    cursor = y
    for name, cfg in stacks.items():
        panels.extend(_stack_row(name, cfg, cursor))
        cursor += _ROW_H
    return panels


def _max_y(panels: list[dict[str, Any]], default: int) -> int:
    """The first free y below `panels` (their max bottom edge), or `default` when empty."""
    return max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels), default=default)


def build_watcher(topo: dict[str, Any]) -> dict[str, Any]:
    """Pure builder: the topology dict → the Watcher dashboard dict. Object-driven — every
    support host and every stack renders a row; no I/O."""
    panels: list[dict[str, Any]] = [_title_banner(y=0)]
    panels.append(_row_header("① Support layer — DNS · obs · probe · svc · app", y=2))
    support = _support_rows(topo, y=3)
    panels.extend(support)
    stacks_y = _max_y(support, default=3)
    panels.append(_row_header("② Stacks — live/DR posture + flow", y=stacks_y))
    panels.extend(_stack_rows(topo, y=stacks_y + 1))
    return {
        "uid": DASHBOARD_UID,
        "title": "The Watcher · Lab State",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "watcher"],
    }


def watcher_dashboard_path() -> Path:
    """Where the rendered Watcher board is written — beside the cockpits (gitignored)."""
    return work("grafana", "dashboards", "lab-watcher.json")


def lab_watcher_dashboard() -> str:
    """Render the real lab/topology.yaml to the Watcher dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(build_watcher(topo), indent=2) + "\n"
