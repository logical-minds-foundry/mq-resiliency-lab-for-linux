"""Pure builders for the cluster-cockpit board (lab-pcmk-cluster). Data in → Grafana
panel/dashboard dicts out, no I/O — mirrors dashboard.py. Engine = Table (spike §4.1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

Column = tuple[str, str, str]  # (title, promql, mapping_kind)

_GREEN, _RED = "green", "red"
# Colour taxonomy: green = live/active-good; blue = healthy but not the live-active
# one (the "alternate green" — Replica, standby, Recovery); yellow = warning; red = bad.
_BLUE, _YELLOW = "blue", "yellow"
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
    # Native-HA role code → coloured text. The Live group's leader is "Active" (running the QM,
    # green); the Recovery group's leader is "Leader" (the standby that applies CRR replication,
    # blue — healthy but not the live-active one, not a warning). Replica is a healthy follower
    # (also blue). Only a genuinely down/unknown instance is red. So a site reads green-led when
    # live, blue-led when standby (#279 feedback; #399 recolour).
    "role": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "Unknown", "index": 0},
                "1": {"color": _BLUE, "text": "Replica", "index": 1},
                "2": {"color": _GREEN, "text": "Active", "index": 2},
                "3": {"color": _BLUE, "text": "Leader", "index": 3},
            },
        },
    ],
    # RDQM HA role code → coloured text. Primary runs the QM (green leader); Secondary is the
    # healthy DRBD standby (blue); only a genuinely down/unknown instance is red (#287).
    "rdqm_role": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "Unknown", "index": 0},
                "1": {"color": _BLUE, "text": "Secondary", "index": 1},
                "2": {"color": _GREEN, "text": "Primary", "index": 2},
            },
        },
    ],
    # QM-running: 1 → green "running"; 0 → a healthy STANDBY node (the QM is only ever live on
    # one node), shown neutral blue — NOT red "down", which would falsely read as a failure
    # across every standby (#287 feedback).
    "qm_running": [
        {
            "type": "value",
            "options": {
                "0": {"color": _BLUE, "text": "standby", "index": 0},
                "1": {"color": _GREEN, "text": "✓ running", "index": 1},
            },
        },
    ],
    # Node ready: 1 = pacemaker-online AND able to run the QM → green "ready"; 0 = online-but-
    # banned (or offline) → red "blocked". A node that can host nothing must not read green "up"
    # even though pacemaker membership is fine (#287 feedback).
    "node_ready": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "blocked", "index": 0},
                "1": {"color": _GREEN, "text": "ready", "index": 1},
            },
        },
    ],
    # Pacemaker resource state code → text. 2 = Started/Promoted (active, green); 1 = Unpromoted
    # (healthy replica, blue); 0 = Stopped/absent — NEUTRAL grey, not an alarm (a resource being
    # stopped on a standby is normal; the fail-count column is what flags a real ban) (#287).
    "pm_state": [
        {
            "type": "value",
            "options": {
                "0": {"color": "#5a6168", "text": "stopped", "index": 0},
                "1": {"color": _BLUE, "text": "replica", "index": 1},
                "2": {"color": _GREEN, "text": "active", "index": 2},
            },
        },
    ],
    # Pacemaker fail-count → 0 green "ok"; a soft failure (1..<INFINITY) amber "failing"; the
    # pacemaker INFINITY sentinel (1000000 = migration threshold reached) → red "BANNED" (#287).
    "failcount": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 999999,
                "result": {"color": "orange", "text": "failing", "index": 1},
            },
        },
        {
            "type": "range",
            "options": {
                "from": 1000000,
                "to": 1e12,
                "result": {"color": _RED, "text": "BANNED", "index": 2},
            },
        },
    ],
    # DRBD out-of-sync bytes, tolerant: 0..one 4 KiB extent → green (a benign sub-extent
    # secondary↔secondary delta is a normal, expected state and must read green); a real backlog
    # (≥ one extent) → red. The honest byte value stays visible in both ranges (#287 feedback).
    "drbd_oos": [
        {
            "type": "range",
            "options": {"from": 0, "to": 4095, "result": {"color": _GREEN, "index": 0}},
        },
        {
            "type": "range",
            "options": {"from": 4096, "to": 1e12, "result": {"color": _RED, "index": 1}},
        },
    ],
}
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Shared cluster_* metrics (cluster_node_online, cluster_quorate, cluster_resource_owner) are
# emitted by EVERY arm's collector into one Prometheus, so every board query over them MUST be
# scoped to its own arm's ansible groups — otherwise one cluster's nodes leak into another's
# board (#279: the nha arm's nodes showed up on the PCMK board). cluster_resource_owner is
# additionally resource-scoped (mq_qm vs NHARAPP), so it needs no group scope.
_PCMK_SEL = '{groups=~"pcmk_a|pcmk_b"}'
_NHA_SEL = '{groups=~"nha_rhel_a|nha_rhel_b"}'
_RDQM_SEL = '{groups=~"rdqm_a|rdqm_b"}'
_RDQM_GROUPS = 'groups=~"rdqm_a|rdqm_b"'  # bare matcher for injecting into a wider selector


def _ds(uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": uid}


def matrix(
    title: str, columns: list[Column], ds_uid: str, y: int, h: int = 9, x: int = 0, w: int = 24
) -> dict[str, Any]:
    """A node×component Table panel: one normalized query per column, joined on `n`,
    with per-column colour-background cell mappings. Rows are data-driven; `h` sizes the
    panel to its row count. x/w default to a full-width row but can be narrowed to sit beside
    another panel (e.g. a per-site role chip)."""
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
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
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
    ("online", _norm(f"cluster_node_online{_PCMK_SEL}", "member"), "up"),
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


# Compact value-font (px) for the single-row status / CRR bands — caps Grafana's auto-fit so
# the tiles don't waste vertical real estate with huge numbers (#279 feedback).
_COMPACT_VALUE_SIZE = 22


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
    name_label: str = "holder",
    w: int = 6,
    h: int = 4,
    value_size: int | None = None,
) -> dict[str, Any]:
    """A single Stat tile with a sparkline (graphMode=area). text_mode="name" shows the
    name_label value (e.g. the owner holder, or a group's role). w/h size the tile;
    value_size caps the value font (px) to reclaim vertical space in compact one-row bands."""
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
        target["legendFormat"] = f"{{{{{name_label}}}}}"
    options: dict[str, Any] = {
        "graphMode": "area",
        "textMode": text_mode,
        "reduceOptions": {"calcs": ["lastNotNull"]},
    }
    if value_size is not None:
        options["text"] = {"valueSize": value_size}
    return {
        "type": "stat",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": options,
    }


_STALE_MAP = {
    "type": "special",
    "options": {"match": "null", "result": {"color": "text", "text": "STALE", "index": 9}},
}


def hero_tiles(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """The top band: cluster health, nodes online, active QM owner, replication backlog.
    No-data reads STALE, never healthy (fail-loud)."""
    online = f"max by (member)(cluster_node_online{_PCMK_SEL})"
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


_INTEGRITY_MAPS = [
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


def _integrity_from_expr(expr: str, ds_uid: str, y: int) -> dict[str, Any]:
    """A first-class integrity light from a hazard expr: 0 → green, ≥1 → HAZARD, no-data →
    STALE (full-width banner). The hazard expr is arm-specific; the colour/STALE vocabulary
    is shared."""
    panel = _stat("Integrity", expr, ds_uid, 0, y, mappings=_INTEGRITY_MAPS)
    panel["gridPos"]["w"] = 24  # full-width banner
    return panel


def _nativeha_integrity_expr() -> str:
    """Native HA cannot split-brain (raft quorum). The hazard reframes around availability +
    durability: quorum-lost ∨ no-Active ∨ replica-not-in-sync, gated on data present so
    no-data reads STALE (spec §6)."""
    hazards = (
        f"(min(cluster_quorate{_NHA_SEL}) == bool 0)"
        ' + (absent(cluster_resource_owner{resource="NHARAPP"}) or vector(0))'
        " + (count(cluster_nha_insync == 0) or vector(0))"
    )
    return f"({hazards}) and on() (count(cluster_nha_role) > 0)"


def nativeha_status_band(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """① Cluster status as ONE compact full-width row of five equal tiles — Active instance ·
    Quorum · Instances in-sync · HA status · Integrity. Integrity is a tile among equals (not a
    full-width banner) and the value font is capped to reclaim vertical space (#279 feedback)."""
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
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        _stat(
            # max by (holder) collapses the per-reporter series → one tile (the Active instance)
            "Active instance",
            'max by (holder)(cluster_resource_owner{resource="NHARAPP"})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
        ),
        _stat("Quorum", "max(cluster_nha_quorum)", ds_uid, 5, y, w=4, h=h, value_size=vs),
        _stat(
            "Instances in-sync",
            "sum(max by (member)(cluster_nha_insync))",
            ds_uid,
            9,
            y,
            w=5,
            h=h,
            value_size=vs,
        ),
        _stat(
            "HA status",
            f"min({normal})",
            ds_uid,
            14,
            y,
            mappings=health_maps,
            w=5,
            h=h,
            value_size=vs,
        ),
        _stat(
            "Integrity",
            _nativeha_integrity_expr(),
            ds_uid,
            19,
            y,
            mappings=_INTEGRITY_MAPS,
            w=5,
            h=h,
            value_size=vs,
        ),
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


_TIMELINE_SIGNALS = [
    ("nodes online", f"sum(max by (member)(cluster_node_online{_PCMK_SEL}))"),
    ("QM running", 'max(cluster_resource_started{resource="mq_qm"})'),
    ("DRBD primary", 'count(cluster_drbd_role{role="Primary"})'),
    ("quorate", f"min(cluster_quorate{_PCMK_SEL})"),
]
# Holder-agnostic owner-change marker: cluster_resource_owner carries the holder in a
# label, so an owner change spawns a NEW series — `changes()` on it won't fire. Count
# owners per resource and watch THAT change instead (§6.4).
_OWNER_CHANGE_EXPR = "changes((count by (resource)(cluster_resource_owner))[5m:])"


def _state_timeline(
    title: str, signals: list[tuple[str, str]], ds_uid: str, y: int
) -> dict[str, Any]:
    """A State-timeline band of named signals over the drill window. The matrix is *now*;
    this shows the cluster moving through a cutover. Arm-specific signals in; panel out."""
    targets = [
        {
            "refId": _REFIDS[i],
            "expr": expr,
            "range": True,
            "legendFormat": name,
            "datasource": _ds(ds_uid),
        }
        for i, (name, expr) in enumerate(signals)
    ]
    return {
        "type": "state-timeline",
        "title": title,
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


def timeline_band(ds_uid: str, y: int) -> dict[str, Any]:
    """The PCMK failover-story band (nodes/QM/DRBD/quorum over the drill window)."""
    return _state_timeline("⟳ Failover timeline", _TIMELINE_SIGNALS, ds_uid, y)


def _logs_panel(
    title: str, selector: str, loki_uid: str, y: int, *, description: str | None = None
) -> dict[str, Any]:
    """An embedded live log row from Loki. The matrix shows *what* changed; this shows *why*,
    on one screen (§6.5). The selector (hosts + units) is arm-specific; severity is the shared
    $level toggle (see _log_level_var) injecting the line-filter regex. An optional description
    surfaces as the panel's info tooltip (e.g. to note which log sources are/aren't wired)."""
    ds = {"type": "loki", "uid": loki_uid}
    panel: dict[str, Any] = {
        "type": "logs",
        "title": title,
        "datasource": ds,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [{"refId": "A", "expr": selector, "datasource": ds}],
        "options": {
            "showTime": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
            "wrapLogMessage": False,
        },
    }
    if description is not None:
        panel["description"] = description
    return panel


def log_row(loki_uid: str, y: int) -> dict[str, Any]:
    """The PCMK cluster-node log row (corosync/pacemaker/drbd/mq units on pcmk-/san- hosts)."""
    sel = '{host=~"pcmk-.*|san-.*", unit=~"corosync.*|pacemaker.*|drbd.*|.*mq.*"} |~ `${level}`'
    return _logs_panel("▤ Cluster logs (severity: $level)", sel, loki_uid, y)


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


# ── Native HA: timeline · logs · CRR card · perf · network ────────────────────

# collapse per-member first (each node reports every member it sees) so counts/sums are the
# real instance count, not multiplied by the number of reporters.
_NHA_TIMELINE_SIGNALS = [
    ("Active instances", 'count(max by (member)(cluster_nha_role{role="Active"}))'),
    ("quorum", "max(cluster_nha_quorum)"),
    ("instances in-sync", "sum(max by (member)(cluster_nha_insync))"),
    ("CRR connected", 'max(cluster_nha_connected{group="Recovery"})'),
]


def _nativeha_timeline(ds_uid: str, y: int) -> dict[str, Any]:
    """The Native HA failover + CRR story: Active count, quorum, in-sync, CRR-connected."""
    return _state_timeline("⟳ Failover & CRR timeline", _NHA_TIMELINE_SIGNALS, ds_uid, y)


def _nativeha_log_row(loki_uid: str, y: int) -> dict[str, Any]:
    """Native HA logs: MQ-related journald units on the nha-rhel hosts, severity-filtered by
    the shared $level toggle. Note: MQ's own error log (AMQERR*.LOG) is file-based, not
    journald — so the QM's HA/CRR events only appear here once Alloy tails those files."""
    sel = '{host=~"nha-rhel-.*", unit=~".*mqmonitor.*|.*amq.*|.*ibmmq.*|mq-.*"} |~ `${level}`'
    note = (
        "Shows MQ-related journald units on the nha nodes. MQ's own error log "
        "(/var/mqm/qmgrs/NHARAPP/errors/AMQERR*.LOG) is file-based, not journald, so it is "
        "not shipped to Loki yet — wire Alloy to tail those files for full QM HA/CRR logs."
    )
    return _logs_panel("▤ Native HA logs (severity: $level)", sel, loki_uid, y, description=note)


def _site_role_badge(site_regex: str, ds_uid: str, x: int, y: int) -> dict[str, Any]:
    """A bold per-site header badge: LIVE (green) when the site holds the Active instance,
    RECOVERY (blue) when it holds the standby Leader — a healthy standby, not a warning; derived
    from the data so it flips on failover, never a static site label (#279 feedback; #399 recolour).
    Background-coloured so the live/standby split is obvious at a glance, without reading the role
    column."""
    maps = [
        {
            "type": "value",
            "options": {
                "2": {"color": _GREEN, "text": "LIVE", "index": 0},
                "3": {"color": _BLUE, "text": "RECOVERY", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    badge = _stat(
        "",
        f'max(cluster_nha_role_code{{member=~"{site_regex}"}})',
        ds_uid,
        x,
        y,
        mappings=maps,
        w=5,
        h=7,
        value_size=_COMPACT_VALUE_SIZE,
    )
    badge["options"]["colorMode"] = "background"
    badge["options"]["graphMode"] = "none"
    return badge


def nativeha_crr_card(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """③ Cross-region (CRR) replication health, from the `dspmq -g` group view: is the recovery
    group connected, in-sync, and how far behind (backlog). Which site is live/recovery is shown
    in the instances section (the site badges + role column), so it is not repeated here."""
    conn_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "disconnected", "index": 0},
                "1": {"color": _GREEN, "text": "✓ connected", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    insync_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _YELLOW, "text": "catching up", "index": 0},
                "1": {"color": _GREEN, "text": "✓ in-sync", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        _stat(
            "CRR connected",
            'max(cluster_nha_connected{group="Recovery"})',
            ds_uid,
            0,
            y,
            mappings=conn_maps,
            w=8,
            h=h,
            value_size=vs,
        ),
        _stat(
            "CRR in-sync",
            'max(cluster_nha_group_insync{group="Recovery"})',
            ds_uid,
            8,
            y,
            mappings=insync_maps,
            w=8,
            h=h,
            value_size=vs,
        ),
        _stat(
            "CRR backlog",
            'max(cluster_nha_group_backlog{group="Recovery"})',
            ds_uid,
            16,
            y,
            w=8,
            h=h,
            value_size=vs,
        ),
    ]


def nativeha_perf_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """Perf from existing node metrics: CPU busy%, intra-site raft (net-hb) throughput, and
    cross-region CRR (net-wan) throughput. No SAN disk — Native HA has no storage tier."""
    cpu_busy = (
        "100 - (avg by (host)(rate("
        'node_cpu_seconds_total{groups=~"nha_rhel_a|nha_rhel_b", mode="idle"}[1m]'
        ")) * 100)"
    )
    hb_rx = 'rate(node_network_receive_bytes_total{device=~"virbr-hb.*"}[1m])'
    hb_tx = 'rate(node_network_transmit_bytes_total{device=~"virbr-hb.*"}[1m])'
    wan_rx = 'rate(node_network_receive_bytes_total{device=~"virbr-wan.*"}[1m])'
    wan_tx = 'rate(node_network_transmit_bytes_total{device=~"virbr-wan.*"}[1m])'
    return [
        _timeseries(
            "CPU busy % — nha nodes",
            [_t("A", cpu_busy, "{{host}}")],
            ds_uid,
            0,
            y,
            unit="percent",
        ),
        _timeseries(
            "Raft replication (net-hb)",
            [_t("A", hb_rx, "rx"), _t("B", hb_tx, "tx")],
            ds_uid,
            8,
            y,
            unit="Bps",
        ),
        _timeseries(
            "CRR replication (net-wan)",
            [_t("A", wan_rx, "rx"), _t("B", wan_tx, "tx")],
            ds_uid,
            16,
            y,
            unit="Bps",
        ),
    ]


_NHA_PLANES = "net-hb-a|net-hb-b|net-wan|net-data-a|net-data-b|net-ext"


def nativeha_net_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """Network from existing metrics: per-plane state + throughput for the planes Native HA
    rides — raft heartbeat (net-hb), CRR WAN (net-wan), data, and the external mesh link."""
    state = f'lab_network_state{{network=~"{_NHA_PLANES}"}}'
    thru = 'rate(node_network_receive_bytes_total{device=~"virbr-(hb|wan|data|ext).*"}[1m])'
    return [
        _timeseries(
            "Native HA network planes — state",
            [_t("A", state, "{{network}}")],
            ds_uid,
            0,
            y,
            w=12,
        ),
        _timeseries(
            "Native HA network throughput",
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


# ── RDQM: status band · instances · DRBD storage · DR card · timeline · logs · perf/net ──


_MATRIX_MIN_WIDTH = 80  # px floor per column; low enough that 6–7 columns shrink to fit (#300)


def _fit_table(panel: dict[str, Any]) -> dict[str, Any]:
    """Make a matrix() table show exactly its real columns AND fit the panel (#287, #300).

    Two fits:
    - Columns: `joinByField` can carry a leftover Time field per instant query that organize
      doesn't fully exclude; append a filterFieldsByName that keeps ONLY node + the real value
      columns.
    - Width: drop the per-column minWidth to _MATRIX_MIN_WIDTH so Grafana shrinks the 6–7 columns
      to fit the panel instead of overflowing into a horizontal scrollbar. Grafana won't shrink a
      column below its content/min width, so the default floor — not any vertical scrollbar — is
      what forced the scroll; panel heights are right-sized separately for the vertical waste.

    Scoped to the rdqm matrices so the PCMK/NHA boards are untouched."""
    org = next(t for t in panel["transformations"] if t["id"] == "organize")
    keep = list(org["options"]["renameByName"].values())  # ["node", <column titles…>]
    panel["transformations"].append(
        {"id": "filterFieldsByName", "options": {"include": {"names": keep}}}
    )
    panel["fieldConfig"]["defaults"].setdefault("custom", {})["minWidth"] = _MATRIX_MIN_WIDTH
    return panel


def _rdqm_instance_cols(site_regex: str) -> list[Column]:
    """RDQM instances-matrix columns for one site (rdqm-a.* / rdqm-b.*): HA status (online if
    Normal) · role (Primary/Secondary, coded) · QM-running · a single Pacemaker summary cell ·
    DRBD in-sync. The Pacemaker cell (node_ready = online AND able to run the QM) sits between
    QM-running and DRBD so a NOT-READY site shows its cause inline; the per-resource pacemaker
    detail lives one section down in ③ (#287). The per-member columns key on `member`; the DRBD
    column keys on `node` and pins the HA resource (qmrdqm) so the cross-site DR resource
    (qmrdqm.dr) never bleeds into the in-sync cell (spec §5 ②)."""
    member = f'member=~"{site_regex}"'
    node = f'node=~"{site_regex}"'
    return [
        ("HA status", _norm(f"cluster_node_online{{{member}}}", "member"), "up"),
        ("role", _norm(f"cluster_rdqm_role_code{{{member}}}", "member"), "rdqm_role"),
        ("QM running", _norm(f"cluster_rdqm_qm_running{{{member}}}", "member"), "qm_running"),
        ("Pacemaker", _norm(f"cluster_rdqm_node_ready{{{member}}}", "member"), "node_ready"),
        (
            "DRBD in-sync",
            _norm(f'cluster_drbd_disk{{resource="qmrdqm",disk="UpToDate",{node}}}', "node"),
            "up",
        ),
    ]


def _rdqm_site_badge(group: str, ds_uid: str, x: int, y: int) -> dict[str, Any]:
    """A bold per-site header badge derived from the site's DR role AND pacemaker startability,
    so it flips on an rdqmdr cutover and goes loud when the side can't fail over (#287 feedback):

    - LIVE (green, code 2) when the site is the DR primary (holds the running QM),
    - RECOVERY (blue, code 1) when it is the DR standby and a node there can run the QM — blue is
      our standby colour; yellow (warning) was wrong for a healthy standby,
    - NOT READY (red, code 0) when a RECOVERY site is banned and can't take over.

    Selected by the site's FIXED group (rdqm_a / rdqm_b). The startable term is `or vector(0)`-
    guarded so the LIVE side and a not-yet-instrumented RECOVERY side never read a false red.
    """
    sel = f'{{groups=~"{group}"}}'
    role = f"max(cluster_rdqm_dr_role_code{sel})"
    banned = f"(max(cluster_rdqm_qm_startable{sel}) == bool 0 or vector(0))"
    # 2 if DR primary; else (DR secondary) 1 when ready, 0 when banned.
    expr = f"(2 * ({role} == bool 2)) + (({role} == bool 3) * (1 - {banned}))"
    maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "⚠ NOT READY", "index": 0},
                "1": {"color": _BLUE, "text": "RECOVERY", "index": 1},
                "2": {"color": _GREEN, "text": "LIVE", "index": 2},
            },
        },
        _STALE_MAP,
    ]
    badge = _stat("", expr, ds_uid, x, y, mappings=maps, w=4, h=6, value_size=_COMPACT_VALUE_SIZE)
    badge["options"]["colorMode"] = "background"
    badge["options"]["graphMode"] = "none"
    return badge


def _rdqm_integrity_expr() -> str:
    """RDQM is DRBD under Pacemaker, so it CAN split-brain — the integrity light is PCMK-style.
    It goes loud on the DRBD storage hazards (StandAlone / per-resource dual-primary / Diskless)
    PLUS the RDQM HA status ≠ Normal, gated on data present so no-data reads STALE (spec §6).
    DRBD hazards are scoped to rdqm groups so another arm's DRBD can't trip this light."""
    g = _RDQM_GROUPS
    # dual-primary is counted per resource AND per site (groups): a DR pair legitimately runs one
    # qmrdqm primary on each side, so a global per-resource count of 2 is normal, not split-brain.
    hazards = (
        f'(count(cluster_drbd_conn{{conn="StandAlone",{g}}}) or vector(0))'
        f' + (count(cluster_drbd_disk{{disk="Diskless",{g}}}) or vector(0))'
        f' + (sum(count by (resource, groups)(cluster_drbd_role{{role="Primary",{g}}}) > bool 1)'
        " or vector(0))"
        " + (count(cluster_rdqm_ha_status_ok == 0) or vector(0))"
        # pacemaker layer: a whole site (group) with no startable node = the QM can't run there
        # (a live-side outage, or a non-viable DR recovery side) (#287).
        " + (count(max by (groups)(cluster_rdqm_qm_startable) == 0) or vector(0))"
    )
    return f"({hazards}) and on() (count(cluster_rdqm_ha_status_ok) > 0)"


def rdqm_status_band(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """① Cluster status as ONE compact full-width row of five equal tiles — Running-on node ·
    HA status · Nodes online · Floating IP (the single VIP) · Integrity. The floating IP is
    first-class (spec §5 ①); integrity is a tile among equals, value font capped to reclaim
    vertical space (the #279 compaction)."""
    online = f"max by (member)(cluster_node_online{_RDQM_SEL})"
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
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        _stat(
            "Running on",
            'max by (holder)(cluster_resource_owner{resource="RDQMAPP"})',
            ds_uid,
            0,
            y,
            text_mode="name",
            w=5,
            h=h,
            value_size=vs,
        ),
        _stat(
            "HA status",
            "min(cluster_rdqm_ha_status_ok)",
            ds_uid,
            5,
            y,
            mappings=health_maps,
            w=4,
            h=h,
            value_size=vs,
        ),
        _stat("Nodes online", f"sum({online})", ds_uid, 9, y, w=5, h=h, value_size=vs),
        _stat(
            "Floating IP",
            "max by (ip)(cluster_rdqm_floating_ip)",
            ds_uid,
            14,
            y,
            text_mode="name",
            name_label="ip",
            w=5,
            h=h,
            value_size=vs,
        ),
        _stat(
            "Integrity",
            _rdqm_integrity_expr(),
            ds_uid,
            19,
            y,
            mappings=_INTEGRITY_MAPS,
            w=5,
            h=h,
            value_size=vs,
        ),
    ]


def _rdqm_storage_cols() -> list[Column]:
    """③ Storage — DRBD: per-node resync % · out-of-sync for the HA resource (qmrdqm), scoped
    to rdqm nodes. The cross-site DR resource (qmrdqm.dr) is surfaced as the ④ DR backlog, not
    here, so this matrix is the intra-site HA replication health (spec §5 ③)."""
    res = 'resource="qmrdqm",node=~"rdqm-.*"'
    return [
        ("resync %", _norm(f"cluster_drbd_resync_pct{{{res}}}", "node"), "sessions"),
        # tolerant mapping: a benign sub-extent (~1KB) secondary↔secondary delta reads green
        ("out-of-sync", _norm(f"cluster_drbd_out_of_sync_bytes{{{res}}}", "node"), "drbd_oos"),
    ]


def _rdqm_pacemaker_cols(site_regex: str) -> list[Column]:
    """⑤ Pacemaker resources for one site (the technology layer rdqmadm wraps, exposed exactly
    like the PCMK board's compute matrix): per node — online · the QM resource · the HA + DR
    DRBD clones · the floating-IP resource (each as a pm_state cell) · the QM fail-count. The
    fail-count cell is the one that turns RED when pacemaker has banned the QM from a node — the
    failure that rdqmstatus reports as HA-Normal and the board was previously blind to (#287)."""
    member = f'member=~"{site_regex}"'

    def state(resource: str) -> str:
        return _norm(f'cluster_rdqm_pm_state{{resource="{resource}",{member}}}', "member")

    return [
        # "ready" = online AND startable, so a banned-but-online node reads red, not green "up"
        ("ready", _norm(f"cluster_rdqm_node_ready{{{member}}}", "member"), "node_ready"),
        ("QM", state("qmrdqm"), "pm_state"),
        ("DRBD HA", state("p_drbd_qmrdqm"), "pm_state"),
        ("DR repl", state("p_drbd_dr_qmrdqm"), "pm_state"),
        ("float-IP", state("p_ip_qmrdqm"), "pm_state"),
        ("fail-count", _norm(f"cluster_rdqm_failcount{{{member}}}", "member"), "failcount"),
    ]


def rdqm_dr_card(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """④ Cross-site DR (rdqmdr): DR status · which node/site is DR primary · replication
    backlog. rdqmstatus reports no backlog field, so the honest backlog is the DR DRBD
    resource's (qmrdqm.dr) out-of-sync bytes (spec §4/§5 ④)."""
    status_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DEGRADED", "index": 0},
                "1": {"color": _GREEN, "text": "✓ Normal", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    ready_maps = [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "✓ Ready", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "⚠ NOT READY", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    # 0 (a site banned) gated on data present: no startable data → STALE, not a false "Ready".
    failover_ready = (
        "(count(max by (groups)(cluster_rdqm_qm_startable) == 0) or vector(0))"
        " and on() (count(cluster_rdqm_qm_startable) > 0)"
    )
    vs, h = _COMPACT_VALUE_SIZE, 3
    return [
        _stat(
            "DR status",
            "min(cluster_rdqm_dr_status_ok)",
            ds_uid,
            0,
            y,
            mappings=status_maps,
            w=6,
            h=h,
            value_size=vs,
        ),
        _stat(
            "DR primary",
            # name the SIDE that is DR-primary, not its three hosts: collapse the side's nodes
            # with `max by (groups)`, then relabel the group to a friendly site name.
            "label_replace(label_replace("
            'max by (groups)(cluster_rdqm_dr_role{role="Primary"}),'
            '"site","Site A","groups","rdqm_a"),'
            '"site","Site B","groups","rdqm_b")',
            ds_uid,
            6,
            y,
            text_mode="name",
            name_label="site",
            w=6,
            h=h,
            value_size=vs,
        ),
        _stat(
            # the recovery site can actually take over only if a node there can start the QM —
            # the pacemaker-derived signal that exposes a banned/non-viable DR side (#287).
            "Failover ready",
            failover_ready,
            ds_uid,
            12,
            y,
            mappings=ready_maps,
            w=6,
            h=h,
            value_size=vs,
        ),
        _stat(
            "DR backlog",
            'max(cluster_drbd_out_of_sync_bytes{resource="qmrdqm.dr"})',
            ds_uid,
            18,
            y,
            unit="bytes",
            w=6,
            h=h,
            value_size=vs,
        ),
    ]


_RDQM_TIMELINE_SIGNALS = [
    ("QM running", "max(cluster_rdqm_qm_running)"),
    ("HA Normal", "min(cluster_rdqm_ha_status_ok)"),
    ("DRBD primary", 'count(cluster_drbd_role{resource="qmrdqm",role="Primary"})'),
    ("DR connected", "min(cluster_rdqm_dr_status_ok)"),
]


def _rdqm_timeline(ds_uid: str, y: int) -> dict[str, Any]:
    """The RDQM failover story: QM running, HA Normal, DRBD primary present, DR connected."""
    return _state_timeline("⟳ Failover timeline", _RDQM_TIMELINE_SIGNALS, ds_uid, y)


def _rdqm_log_row(loki_uid: str, y: int) -> dict[str, Any]:
    """RDQM logs: the Pacemaker/DRBD/MQ journald units on the rdqm-* hosts, severity-filtered
    by the shared $level toggle. Note: MQ's AMQERR error log is file-based, not journald, so
    the QM's own HA/DR events only appear once Alloy tails those files (same as the other arms)."""
    sel = (
        '{host=~"rdqm-.*", unit=~"pacemaker.*|corosync.*|drbd.*|.*mqmonitor.*|.*amq.*'
        '|.*ibmmq.*|mq-.*"} |~ `${level}`'
    )
    note = (
        "Shows Pacemaker/DRBD/MQ journald units on the rdqm nodes. MQ's own error log "
        "(/var/mqm/qmgrs/RDQMAPP/errors/AMQERR*.LOG) is file-based, not journald, so it is "
        "not shipped to Loki yet — wire Alloy to tail those files for full QM HA/DR logs."
    )
    return _logs_panel("▤ RDQM logs (severity: $level)", sel, loki_uid, y, description=note)


def rdqm_perf_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """Perf from existing node metrics: CPU busy%, intra-site DRBD HA (net-hb) throughput, and
    cross-site DR (net-wan) throughput."""
    cpu_busy = (
        "100 - (avg by (host)(rate("
        'node_cpu_seconds_total{groups=~"rdqm_a|rdqm_b", mode="idle"}[1m]'
        ")) * 100)"
    )
    hb_rx = 'rate(node_network_receive_bytes_total{device=~"virbr-hb.*"}[1m])'
    hb_tx = 'rate(node_network_transmit_bytes_total{device=~"virbr-hb.*"}[1m])'
    wan_rx = 'rate(node_network_receive_bytes_total{device=~"virbr-wan.*"}[1m])'
    wan_tx = 'rate(node_network_transmit_bytes_total{device=~"virbr-wan.*"}[1m])'
    return [
        _timeseries(
            "CPU busy % — rdqm nodes",
            [_t("A", cpu_busy, "{{host}}")],
            ds_uid,
            0,
            y,
            unit="percent",
        ),
        _timeseries(
            "DRBD HA replication (net-hb)",
            [_t("A", hb_rx, "rx"), _t("B", hb_tx, "tx")],
            ds_uid,
            8,
            y,
            unit="Bps",
        ),
        _timeseries(
            "DR replication (net-wan)",
            [_t("A", wan_rx, "rx"), _t("B", wan_tx, "tx")],
            ds_uid,
            16,
            y,
            unit="Bps",
        ),
    ]


_RDQM_PLANES = "net-hb-a|net-hb-b|net-wan|net-data-a|net-data-b|net-ext"


def rdqm_net_section(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """Network from existing metrics: per-plane state + throughput for the planes RDQM rides —
    intra-site DRBD heartbeat (net-hb), cross-site DR (net-wan), data, and the external mesh."""
    state = f'lab_network_state{{network=~"{_RDQM_PLANES}"}}'
    thru = 'rate(node_network_receive_bytes_total{device=~"virbr-(hb|wan|data|ext).*"}[1m])'
    return [
        _timeseries(
            "RDQM network planes — state",
            [_t("A", state, "{{network}}")],
            ds_uid,
            0,
            y,
            w=12,
        ),
        _timeseries(
            "RDQM network throughput",
            [_t("A", thru, "{{device}}")],
            ds_uid,
            12,
            y,
            w=12,
            unit="Bps",
        ),
    ]


def _rdqm_board(ds_uid: str) -> dict[str, Any]:
    """The RDQM cockpit (lab-rdqm-cluster), top-to-bottom: title banner · ① status band ·
    ② Site A/B instance matrices (with LIVE/RECOVERY chips) · ③ DRBD storage · ④ cross-site DR ·
    failover timeline · logs · perf · network. RDQM is the richest arm — Native-HA-style HA
    roles + cross-site DR AND a real DRBD storage section (#287). Each named section is its own
    peer-level collapsible row, as in the nha board."""
    panels = [
        _title_banner("rdqm-rhel", y=0),
        _row_header("① Cluster status — running-on · HA · floating IP · integrity", y=2),
        *rdqm_status_band(ds_uid, y=3),
        # ② Site A / Site B are the FIXED node groups (rdqm_a / rdqm_b). LIVE vs RECOVERY is a
        # *role* that swaps on rdqmdr cutover/failback — a compact chip beside each site matrix.
        # The matrix is x=4,w=20 to leave room for the badge at x=0,w=4; _fit_table sets a small
        # per-column minWidth so the columns shrink to fit (no horizontal scrollbar).
        _row_header("② Instances — Site A & Site B", y=6),
        # Each matrix is sized to its three nodes (header + 3 rows), reflowed snug below the
        # previous section — no padded empty rows (#300). The badge matches the matrix height.
        _rdqm_site_badge("rdqm_a", ds_uid, 0, 7),
        _fit_table(matrix("Site A", _rdqm_instance_cols("rdqm-a.*"), ds_uid, y=7, h=6, x=4, w=20)),
        _rdqm_site_badge("rdqm_b", ds_uid, 0, 13),
        _fit_table(matrix("Site B", _rdqm_instance_cols("rdqm-b.*"), ds_uid, y=13, h=6, x=4, w=20)),
        # ③ The pacemaker resource layer rdqmadm wraps, exposed like the PCMK board's compute
        # matrix and placed in the SAME position (above storage) so an RDQM board and an Ubuntu
        # pacemaker board read top-to-bottom the same way — both ride pacemaker + DRBD. This is
        # the layer rdqmstatus is blind to: a QM pacemaker can't start (fail-count → BANNED)
        # shows here even while HA status reads Normal (#287).
        _row_header("③ Pacemaker resources — Site A & Site B", y=19),
        _fit_table(
            matrix("Pacemaker — Site A", _rdqm_pacemaker_cols("rdqm-a.*"), ds_uid, y=20, h=6)
        ),
        _fit_table(
            matrix("Pacemaker — Site B", _rdqm_pacemaker_cols("rdqm-b.*"), ds_uid, y=26, h=6)
        ),
        # ④ Storage spans all six nodes (site-A HA group + site-B DR group both run qmrdqm), so
        # it carries six rows + header — h=10 fits all six with a small margin; _fit_table's
        # minWidth keeps the horizontal scrollbar away.
        _row_header("④ Storage — DRBD (qmrdqm)", y=32),
        _fit_table(matrix("Storage — DRBD", _rdqm_storage_cols(), ds_uid, y=33, h=10)),
        _row_header("⑤ Cross-site DR (rdqmdr)", y=43),
        *rdqm_dr_card(ds_uid, y=44),
        _row_header("⟳ Failover timeline", y=47),
        _rdqm_timeline(ds_uid, y=48),
        _row_header("▤ RDQM logs", y=55),
        _rdqm_log_row("loki", y=56),
        _row_header("🖥 Performance", y=64),
        *rdqm_perf_section(ds_uid, y=65),
        _row_header("🌐 Network", y=72),
        *rdqm_net_section(ds_uid, y=73),
    ]
    return {
        "uid": "lab-rdqm-cluster",
        "title": "RDQM Cluster · Infrastructure View",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [_log_level_var()]},
        "annotations": _annotations(ds_uid),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", "rdqm-rhel"],
    }


_ARM_NAMES = {
    "pcmk": "Pacemaker HA + cross-site DR · DRBD/iSCSI SAN · Ubuntu 24.04 (arm64)",
    "nativeha-rhel": "MQ raft Native HA + CRR cross-region · RHEL 9.6 (x86_64)",
    "rdqm-rhel": "DRBD + Pacemaker HA (rdqmadm) + cross-site DR (rdqmdr) · RHEL 9 (x86_64)",
}
_ARM_KIND = {
    "pcmk": "PCMK Cluster",
    "nativeha-rhel": "Native HA Cluster",
    "rdqm-rhel": "RDQM Cluster",
}


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
    """The Native HA cockpit (lab-nativeha-cluster), top-to-bottom: title banner · ① hero +
    integrity · ② instances matrices (Live / Recovery) · ③ CRR card · failover+CRR timeline ·
    logs · perf · network. No storage section — Native HA has no DRBD/SAN tier."""
    # Each named section gets its own peer-level row so they collapse independently and the
    # board reads consistently top-to-bottom (#279 feedback). A row absorbs the panels between
    # it and the next row.
    panels = [
        _title_banner("nativeha-rhel", y=0),
        # ① one compact full-width row of five equal tiles (integrity is a tile, not a banner).
        _row_header("① Cluster status — active · quorum · in-sync · integrity", y=2),
        *nativeha_status_band(ds_uid, y=3),
        # ② Site A / Site B are the FIXED node groups (nha_rhel_a / nha_rhel_b). Live vs Recovery
        # is a *role* that swaps on DR cutover/failback — never a static site label (#279). A
        # compact LIVE/RECOVERY colour chip sits beside each site matrix (the role column gives
        # the per-instance detail); the matrix narrows to w=19 to make room.
        _row_header("② Instances — Site A & Site B", y=6),
        _site_role_badge("nha-rhel-a.*", ds_uid, 0, 7),
        matrix("Site A", _nativeha_instance_cols("nha-rhel-a.*"), ds_uid, y=7, h=7, x=5, w=19),
        _site_role_badge("nha-rhel-b.*", ds_uid, 0, 14),
        matrix("Site B", _nativeha_instance_cols("nha-rhel-b.*"), ds_uid, y=14, h=7, x=5, w=19),
        _row_header("③ Cross-region replication (CRR)", y=21),
        *nativeha_crr_card(ds_uid, y=22),
        _row_header("⟳ Failover & CRR timeline", y=25),
        _nativeha_timeline(ds_uid, y=26),
        _row_header("▤ Native HA logs", y=33),
        _nativeha_log_row("loki", y=34),
        _row_header("🖥 Performance", y=42),
        *nativeha_perf_section(ds_uid, y=43),
        _row_header("🌐 Network", y=50),
        *nativeha_net_section(ds_uid, y=51),
    ]
    return {
        "uid": "lab-nativeha-cluster",
        "title": "Native HA Cluster · Infrastructure View",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [_log_level_var()]},
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
    if arm == "rdqm-rhel":
        return _rdqm_board(ds_uid)
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
    return work("grafana", "dashboards", "lab-pcmk-cluster.json")


def lab_cluster_dashboard() -> str:
    """Render the real lab/topology.yaml to cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo), indent=2) + "\n"


def nativeha_dashboard_path() -> Path:
    """Where the rendered Native HA cockpit board is written (gitignored)."""
    return work("grafana", "dashboards", "lab-nativeha-cluster.json")


def lab_nativeha_dashboard() -> str:
    """Render the real lab/topology.yaml to the Native HA cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo, arm="nativeha-rhel"), indent=2) + "\n"


def rdqm_dashboard_path() -> Path:
    """Where the rendered RDQM cockpit board is written (gitignored)."""
    return work("grafana", "dashboards", "lab-rdqm-cluster.json")


def lab_rdqm_dashboard() -> str:
    """Render the real lab/topology.yaml to the RDQM cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo, arm="rdqm-rhel"), indent=2) + "\n"
