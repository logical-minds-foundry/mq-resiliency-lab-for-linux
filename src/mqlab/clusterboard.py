"""Pure builders for the cluster-cockpit board (lab-pcmk-cluster). Data in → Grafana
panel/dashboard dicts out, no I/O — mirrors dashboard.py. Engine = Table (spike §4.1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

Column = tuple[str, str, str]  # (title, promql, mapping_kind)

_GREEN, _RED = "green", "red"
_MAPPINGS: dict[str, list[dict[str, Any]]] = {
    # 1 → green, 0 → red (daemon up, node online)
    "up": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "down", "index": 0},
                "1": {"color": _GREEN, "text": "up", "index": 1},
            },
        },
    ],
    # 0 → green, ≥1 → red (fence count, unclean)
    "clean0": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "!", "index": 1},
            },
        },
    ],
    # ≥1 → green, 0 → red (iSCSI sessions, resync %)
    "sessions": [
        {"type": "value", "options": {"0": {"color": _RED, "text": "none", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _GREEN, "text": "ok", "index": 1},
            },
        },
    ],
    # Native-HA role code → coloured text: 2 Active (green) · 1 Replica (blue) · 0 Unknown (red)
    "role": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "Unknown", "index": 0},
                "1": {"color": "blue", "text": "Replica", "index": 1},
                "2": {"color": _GREEN, "text": "Active", "index": 2},
            },
        },
    ],
}
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _ds(uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": uid}


def matrix(title: str, columns: list[Column], ds_uid: str, y: int, h: int = 9) -> dict[str, Any]:
    """A node×component Table panel: one normalized query per column, joined on `n`,
    with per-column colour-background cell mappings. Rows are data-driven; `h` sizes the
    panel to its row count (e.g. the 2-row storage matrix is shorter than 6-row compute)."""
    targets: list[dict[str, Any]] = []
    rename: dict[str, str] = {"n": "node"}
    overrides: list[dict[str, Any]] = []
    for i, (col_title, expr, kind) in enumerate(columns):
        ref = _REFIDS[i]
        targets.append(
            {
                "refId": ref,
                "expr": expr,
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            },
        )
        rename[f"Value #{ref}"] = col_title
        overrides.append(
            {
                "matcher": {"id": "byName", "options": col_title},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "basic"},
                    },
                    {"id": "mappings", "value": _MAPPINGS[kind]},
                    {"id": "color", "value": {"mode": "fixed"}},
                ],
            },
        )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "n", "mode": "outer"}},
            {
                "id": "organize",
                "options": {"renameByName": rename, "excludeByName": {"Time": True}},
            },
            # sort rows by node so site A (a1/a2/a3) groups before site B (b1/b2/b3)
            {"id": "sortBy", "options": {"sort": [{"field": "node"}]}},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "center"}},
            "overrides": overrides,
        },
        "options": {"cellHeight": "sm"},
    }


_PRECEDENCE = ("STALE", "red", "amber", "green")


def fold_side(cells: list[str]) -> str:
    """Fold a side's cell states to one signal, worst-wins with STALE first
    (precedence STALE > red > amber > green). Empty or unrecognized → STALE (fail-loud:
    a blind/untrustworthy side must never read as healthy)."""
    for level in _PRECEDENCE:
        if level in cells:
            return level
    return "STALE"


def active_side(owner_sites: list[str]) -> str:
    """The active site from the set of sites currently holding mq_* owners:
    one site → "A"/"B"; none → "none" (mid-transition); two → "split" (hazard)."""
    sites = set(owner_sites)
    if len(sites) > 1:
        return "split"
    if not sites:
        return "none"
    return sites.pop()


def _norm(series: str, label: str) -> str:
    """The row-key-normalized per-column query: collapse node|member|holder → `n`."""
    return f'max by (n)(label_replace({series},"n","$1","{label}","(.*)"))'


_COMPUTE_COLS: list[Column] = [
    ("corosync", _norm('cluster_daemon_up{unit="corosync"}', "node"), "up"),
    ("pacemaker", _norm('cluster_daemon_up{unit="pacemaker"}', "node"), "up"),
    ("iSCSI", _norm("cluster_iscsi_sessions", "node"), "sessions"),
    ("fence", _norm("cluster_fence_count", "member"), "clean0"),
    ("online", _norm("cluster_node_online", "member"), "up"),
    ("unclean", _norm("cluster_node_unclean", "member"), "clean0"),
]
_STORAGE_COLS: list[Column] = [
    ("resync %", _norm("cluster_drbd_resync_pct", "node"), "sessions"),
    ("out-of-sync", _norm("cluster_drbd_out_of_sync_bytes", "node"), "clean0"),
]


def _nativeha_instance_cols(site_regex: str) -> list[Column]:
    """Native-HA instances-matrix columns for one site (member regex selects nha-rhel-a.* /
    -b.*): online · role (coded → Active/Replica/Unknown) · in-sync · HA Normal. No
    corosync/pacemaker/iSCSI/DRBD/fence — Native HA has none (spec §5 ②)."""
    member = f'member=~"{site_regex}"'
    return [
        ("online", _norm(f"cluster_node_online{{{member}}}", "member"), "up"),
        ("role", _norm(f"cluster_nha_role_code{{{member}}}", "member"), "role"),
        ("in-sync", _norm(f"cluster_nha_insync{{{member}}}", "member"), "up"),
        ("HA Normal", _norm(f"cluster_nha_hastatus_ok{{{member}}}", "member"), "up"),
    ]


def _stat(
    title: str,
    expr: str,
    ds_uid: str,
    x: int,
    y: int,
    *,
    mappings: list[dict[str, Any]] | None = None,
    unit: str | None = None,
    text_mode: str = "value",
) -> dict[str, Any]:
    """A single Stat tile with a sparkline (graphMode=area)."""
    defaults: dict[str, Any] = {"mappings": mappings or []}
    if unit is not None:
        defaults["unit"] = unit
    target: dict[str, Any] = {
        "refId": "A",
        "expr": expr,
        "instant": True,
        "datasource": _ds(ds_uid),
    }
    if text_mode == "name":
        target["legendFormat"] = "{{holder}}"
    return {
        "type": "stat",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 4, "w": 6, "x": x, "y": y},
        "targets": [target],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "graphMode": "area",
            "textMode": text_mode,
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
    }


_STALE_MAP = {
    "type": "special",
    "options": {"match": "null", "result": {"color": "text", "text": "STALE", "index": 9}},
}


def hero_tiles(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """The top band: cluster health, nodes online, active QM owner, replication backlog.
    No-data reads STALE, never healthy (fail-loud)."""
    online = "max by (member)(cluster_node_online)"
    health_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DOWN", "index": 0},
                "1": {"color": _GREEN, "text": "✓ healthy", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    return [
        _stat("Cluster health", f"min({online})", ds_uid, 0, y, mappings=health_maps),
        _stat("Nodes online", f"sum({online})", ds_uid, 6, y),
        _stat(
            # max by (holder) collapses the per-reporter series → one tile, not one per node
            "Active QM owner",
            'max by (holder)(cluster_resource_owner{resource="mq_qm"})',
            ds_uid,
            12,
            y,
            text_mode="name",
        ),
        _stat(
            "Replication backlog",
            "max(cluster_drbd_out_of_sync_bytes)",
            ds_uid,
            18,
            y,
            unit="bytes",
        ),
    ]


def _integrity_from_expr(expr: str, ds_uid: str, y: int) -> dict[str, Any]:
    """A first-class integrity light from a hazard expr: 0 → green, ≥1 → HAZARD, no-data →
    STALE (full-width banner). The hazard expr is arm-specific; the colour/STALE vocabulary
    is shared."""
    maps = [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "✓ integrity", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "⚠ HAZARD", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    panel = _stat("Integrity", expr, ds_uid, 0, y, mappings=maps)
    panel["gridPos"]["w"] = 24  # full-width banner
    return panel


def nativeha_hero_tiles(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """The Native HA top band: Active instance · Quorum (in-sync node count) · Instances
    in-sync · HA status. No-data reads STALE, never healthy (fail-loud)."""
    normal = 'max by (member)(cluster_nha_hastatus{status="Normal"})'
    health_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DOWN", "index": 0},
                "1": {"color": _GREEN, "text": "✓ Normal", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    return [
        _stat(
            # max by (holder) collapses the per-reporter series → one tile (the Active instance)
            "Active instance",
            'max by (holder)(cluster_resource_owner{resource="QMNATIVE"})',
            ds_uid,
            0,
            y,
            text_mode="name",
        ),
        _stat("Quorum", "max(cluster_nha_quorum)", ds_uid, 6, y),
        _stat("Instances in-sync", "sum(max by (member)(cluster_nha_insync))", ds_uid, 12, y),
        _stat("HA status", f"min({normal})", ds_uid, 18, y, mappings=health_maps),
    ]


def integrity_panel(ds_uid: str, y: int) -> dict[str, Any]:
    """First-class integrity light: hazard count (split-brain/dual-primary/Diskless),
    gated on DRBD being present so no-data reads STALE (not a false green)."""
    hazards = (
        '(count(cluster_drbd_conn{conn="StandAlone"}) or vector(0))'
        ' + (count(cluster_drbd_disk{disk="Diskless"}) or vector(0))'
        ' + (count(cluster_drbd_role{role="Primary"}) > bool 1)'
    )
    expr = f"({hazards}) and on() (count(cluster_drbd_role) > 0)"
    return _integrity_from_expr(expr, ds_uid, y)


def nativeha_integrity_panel(ds_uid: str, y: int) -> dict[str, Any]:
    """Native HA cannot split-brain (raft quorum). The hazard reframes around availability +
    durability: quorum-lost ∨ no-Active ∨ replica-not-in-sync, gated on data present so
    no-data reads STALE (spec §6)."""
    hazards = (
        "(min(cluster_quorate) == bool 0)"
        ' + (absent(cluster_resource_owner{resource="QMNATIVE"}) or vector(0))'
        " + (count(cluster_nha_insync == 0) or vector(0))"
    )
    expr = f"({hazards}) and on() (count(cluster_nha_role) > 0)"
    return _integrity_from_expr(expr, ds_uid, y)


_TIMELINE_SIGNALS = [
    ("nodes online", "sum(max by (member)(cluster_node_online))"),
    ("QM running", 'max(cluster_resource_started{resource="mq_qm"})'),
    ("DRBD primary", 'count(cluster_drbd_role{role="Primary"})'),
    ("quorate", "min(cluster_quorate)"),
]
# Holder-agnostic owner-change marker: cluster_resource_owner carries the holder in a
# label, so an owner change spawns a NEW series — `changes()` on it won't fire. Count
# owners per resource and watch THAT change instead (§6.4).
_OWNER_CHANGE_EXPR = "changes((count by (resource)(cluster_resource_owner))[5m:])"


def timeline_band(ds_uid: str, y: int) -> dict[str, Any]:
    """The failover-story band: a State-timeline of the key signals over the drill window.
    The matrix is *now*; this shows the cluster moving through a cutover."""
    targets = [
        {
            "refId": _REFIDS[i],
            "expr": expr,
            "range": True,
            "legendFormat": name,
            "datasource": _ds(ds_uid),
        }
        for i, (name, expr) in enumerate(_TIMELINE_SIGNALS)
    ]
    return {
        "type": "state-timeline",
        "title": "⟳ Failover timeline",
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 7, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "custom": {"fillOpacity": 80},
                "color": {"mode": "thresholds"},
                # colour by value: 0 → red (down/lost), ≥1 → green (present). Without this
                # Grafana auto-palettes the raw count (6) and picked red (#219 feedback).
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}],
                },
                "mappings": [
                    {"type": "value", "options": {"0": {"text": "down"}, "1": {"text": "up"}}},
                ],
            },
            "overrides": [],
        },
        "options": {"mergeValues": True, "showValue": "auto"},
    }


def log_row(loki_uid: str, y: int) -> dict[str, Any]:
    """The embedded live log row: cluster-node journald units, severity-filtered (WARN+).
    The matrix shows *what* changed; this shows *why*, on one screen (§6.5)."""
    ds = {"type": "loki", "uid": loki_uid}
    # Severity is a dashboard toggle ($level, see _log_level_var): defaults to WARN+,
    # flip to "All" for info-level. The variable injects the line-filter regex (#219).
    expr = '{host=~"pcmk-.*|san-.*", unit=~"corosync.*|pacemaker.*|drbd.*|.*mq.*"} |~ `${level}`'
    return {
        "type": "logs",
        "title": "▤ Cluster logs (severity: $level)",
        "datasource": ds,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [{"refId": "A", "expr": expr, "datasource": ds}],
        "options": {
            "showTime": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
            "wrapLogMessage": False,
        },
    }


def _timeseries(
    title: str,
    targets: list[dict[str, Any]],
    ds_uid: str,
    x: int,
    y: int,
    *,
    w: int = 8,
    h: int = 7,
    unit: str | None = None,
) -> dict[str, Any]:
    """A timeseries panel from existing node/host metrics (no new telemetry)."""
    defaults: dict[str, Any] = {}
    if unit is not None:
        defaults["unit"] = unit
    return {
        "type": "timeseries",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [{**t, "datasource": _ds(ds_uid)} for t in targets],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {},
    }


_CLUSTER_GROUPS = "pcmk_a|pcmk_b|san_a|san_b"


def _t(ref: str, expr: str, legend: str) -> dict[str, Any]:
    """A timeseries target (range query) with a legend."""
    return {"refId": ref, "expr": expr, "legendFormat": legend, "range": True}


def perf_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """§3 perf from existing metrics: CPU busy%, SAN disk I/O, DRBD (net-wan) throughput."""
    cpu_busy = (
        "100 - (avg by (host)(rate("
        f'node_cpu_seconds_total{{groups=~"{_CLUSTER_GROUPS}", mode="idle"}}[1m]'
        ")) * 100)"
    )
    disk_r = 'rate(node_disk_read_bytes_total{host=~"san-.*"}[1m])'
    disk_w = 'rate(node_disk_written_bytes_total{host=~"san-.*"}[1m])'
    wan_rx = 'rate(node_network_receive_bytes_total{device="virbr-wan"}[1m])'
    wan_tx = 'rate(node_network_transmit_bytes_total{device="virbr-wan"}[1m])'
    return [
        _timeseries(
            "CPU busy % — cluster nodes",
            [_t("A", cpu_busy, "{{host}}")],
            ds_uid,
            0,
            y,
            unit="percent",
        ),
        _timeseries(
            "SAN disk I/O",
            [_t("A", disk_r, "{{host}} read"), _t("B", disk_w, "{{host}} write")],
            ds_uid,
            8,
            y,
            unit="Bps",
        ),
        _timeseries(
            "DRBD throughput (net-wan)",
            [_t("A", wan_rx, "rx"), _t("B", wan_tx, "tx")],
            ds_uid,
            16,
            y,
            unit="Bps",
        ),
    ]


_CLUSTER_PLANES = "net-hb-a|net-hb-b|net-san-a|net-san-b|net-wan|net-data-a|net-data-b"


def net_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """§4 network from existing metrics: per-plane state + throughput, scoped to the
    planes this cluster rides (heartbeat, SAN, WAN replication, data)."""
    state = f'lab_network_state{{network=~"{_CLUSTER_PLANES}"}}'
    thru = 'rate(node_network_receive_bytes_total{device=~"virbr-(hb|san|wan|data).*"}[1m])'
    return [
        _timeseries(
            "Cluster network planes — state",
            [_t("A", state, "{{network}}")],
            ds_uid,
            0,
            y,
            w=12,
        ),
        _timeseries(
            "Cluster network throughput",
            [_t("A", thru, "{{device}}")],
            ds_uid,
            12,
            y,
            w=12,
            unit="Bps",
        ),
    ]


_WARN_REGEX = "(?i)warn|error|fail|fenc|crit|alert|emerg"


def _log_level_var() -> dict[str, Any]:
    """Dashboard toggle for the log row's severity: WARN+ (default) or All (incl. info).
    The selected value is the line-filter regex the log query interpolates ($level)."""
    warn = {"text": "WARN+", "value": _WARN_REGEX, "selected": True}
    show_all = {"text": "All (incl. info)", "value": ".", "selected": False}
    return {
        "name": "level",
        "type": "custom",
        "label": "Log severity",
        "multi": False,
        "includeAll": False,
        "query": f"WARN+ : {_WARN_REGEX}, All (incl. info) : .",
        "options": [warn, show_all],
        "current": warn,
    }


def _annotations(ds_uid: str) -> dict[str, Any]:
    return {
        "list": [
            {
                # always-on, no top-bar toggle (enable+hide): a QM failover is always
                # worth marking, and the toggle just confused (#219 feedback).
                "name": "QM failover (owner change)",
                "datasource": _ds(ds_uid),
                "enable": True,
                "hide": True,
                "iconColor": "orange",
                "expr": _OWNER_CHANGE_EXPR,
                "step": "10s",
            },
        ],
    }


_ARM_NAMES = {
    "pcmk": "Pacemaker HA + cross-site DR · DRBD/iSCSI SAN · Ubuntu 24.04 (arm64)",
    "nativeha-rhel": "MQ raft Native HA + CRR cross-region · RHEL 9.6 (x86_64)",
}
_ARM_KIND = {"pcmk": "PCMK Cluster", "nativeha-rhel": "Native HA Cluster"}


def _title_banner(arm: str, y: int) -> dict[str, Any]:
    """A spelled-out title across the top naming this cluster (#219 feedback)."""
    name = _ARM_NAMES.get(arm, arm)
    kind = _ARM_KIND.get(arm, "Cluster")
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": f"## {kind} · {name}"},
    }


def _row_header(title: str, y: int) -> dict[str, Any]:
    """A section header row."""
    return {
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def _nativeha_board(ds_uid: str) -> dict[str, Any]:
    """The Native HA cockpit (lab-nativeha-cluster): title banner + ① hero/integrity + the two
    ② instances matrices (Live site A / Recovery site B). CRR card + timeline (PR3), logs +
    perf/net (PR4) land in later PRs (spec §8)."""
    panels = [
        _title_banner("nativeha-rhel", y=0),
        _row_header("① Cluster status — active · quorum · in-sync · integrity", y=2),
        *nativeha_hero_tiles(ds_uid, y=3),
        nativeha_integrity_panel(ds_uid, y=7),
        # one matrix per group, banded Live (site A) / Recovery (site B); each is 3 rows +
        # header (h=5). No corosync/pacemaker/iSCSI/DRBD/fence — Native HA has none.
        matrix(
            "② Instances — Live (site A)",
            _nativeha_instance_cols("nha-rhel-a.*"),
            ds_uid,
            y=10,
            h=5,
        ),
        matrix(
            "② Instances — Recovery (site B)",
            _nativeha_instance_cols("nha-rhel-b.*"),
            ds_uid,
            y=15,
            h=5,
        ),
    ]
    return {
        "uid": "lab-nativeha-cluster",
        "title": "Native HA Cluster · Infrastructure View",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": []},
        "annotations": _annotations(ds_uid),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", "nativeha-rhel"],
    }


def render_cluster_dashboard(
    topo: dict[str, Any],  # noqa: ARG001 - reserved: later PRs derive rows/sites from topology
    arm: str = "pcmk",
    ds_uid: str = "prometheus",
) -> dict[str, Any]:
    """Assemble the cockpit board for the given arm. PCMK: hero + integrity + ② Compute / ③
    Storage matrices + timeline/logs/perf/net. Native HA dispatches to its own assembly
    (instances matrices, the §6 integrity reframing)."""
    if arm == "nativeha-rhel":
        return _nativeha_board(ds_uid)
    panels = [
        _title_banner(arm, y=0),
        _row_header("① Cluster status — health · owner · quorum · integrity", y=2),
        *hero_tiles(ds_uid, y=3),
        integrity_panel(ds_uid, y=7),
        matrix("② Compute — node × component", _COMPUTE_COLS, ds_uid, y=10),
        # storage has only san-a/san-b — size it to two rows + header (h=5 so san-b
        # isn't clipped), don't waste the space (#219)
        matrix("③ Storage — DRBD / SAN", _STORAGE_COLS, ds_uid, y=19, h=5),
        timeline_band(ds_uid, y=24),
        log_row("loki", y=31),
        *perf_section(ds_uid, y=39),
        *net_section(ds_uid, y=46),
    ]
    return {
        "uid": "lab-pcmk-cluster",
        "title": "PCMK Cluster · Infrastructure View",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [_log_level_var()]},
        "annotations": _annotations(ds_uid),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", arm],
    }


def cluster_dashboard_path() -> Path:
    """Where the rendered cockpit board is written — beside lab-status.json (gitignored)."""
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"


def lab_cluster_dashboard() -> str:
    """Render the real lab/topology.yaml to cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo), indent=2) + "\n"
