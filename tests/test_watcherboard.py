"""The Watcher board (#488) — lab-state dashboard coverage.

Pure builder tests: a support row per commons support host, a rollup row per stack,
uid pinned, object-driven (every row present at all times → a missing signal reads
as a coloured absence, never a vanished row). Plus a real-topology smoke test in the
style of tests/test_topology_integrity.py.
"""

from __future__ import annotations

import json

import yaml

from mqlab.paths import repo_root
from mqlab.watcherboard import (
    _NEUTRAL,
    _QMSTATUS_MAP,
    _STACK_COLS,
    _SUPPORT_COLS,
    _UTIL,
    DASHBOARD_UID,
    build_watcher,
    lab_watcher_dashboard,
    watcher_dashboard_path,
)


def _by_title(panels: list[dict], title: str) -> list[dict]:
    return [p for p in panels if p.get("title") == title]


def _thresholds(panel: dict) -> list[dict]:
    return panel.get("fieldConfig", {}).get("defaults", {}).get("thresholds", {}).get("steps", [])


# A minimal fixture topology: the five commons support groups (infra carries two DNS
# hosts) + two stacks — pacemaker-san (owner resource = the pacemaker id "mq_qm") and
# rdqm (owner resource = the short-derived QM name). Both stacks carry site-A + site-B
# groups so the site split (and SAN fold-in) is exercised.
FIXTURE: dict = {
    "groups": {
        "infra": ["infra-client", "infra-svc"],
        "obs_box": ["obs"],
        "probe": ["mon-probe"],
        "svc": ["svc-sim"],
        "app": ["app-client"],
        "san_a": ["san-a"],
        "san_b": ["san-b"],
        "pcmk_a": ["pcmk-a1"],
        "pcmk_b": ["pcmk-b1"],
        "rdqm_a": ["rdqm-a1"],
        "rdqm_b": ["rdqm-b1"],
    },
    "svc": {"short": "SVC"},
    "stacks": {
        "pcmk-ubuntu": {
            "mechanism": "pacemaker-san",
            "short": "PCMK",
            "groups": ["san_a", "pcmk_a", "san_b", "pcmk_b"],
        },
        "rdqm-rhel": {
            "mechanism": "rdqm",
            "short": "RDQM",
            "groups": ["rdqm_a", "rdqm_b"],
        },
    },
}

# The curated support-host order the board must emit (infra pair first, then obs, probe,
# svc, app) — driven off the commons groups, NOT hardcoded in the builder.
SUPPORT_HOSTS = ["infra-client", "infra-svc", "obs", "mon-probe", "svc-sim", "app-client"]

EMPTY: dict = {"groups": {}, "svc": {}, "stacks": {}}

# Edge fixture: a support group present but empty (no rows for it), and a stack that
# declares no groups (row still present — object-driven).
EDGE: dict = {
    "groups": {
        "infra": ["infra-client", "infra-svc"],
        "obs_box": [],
        "probe": [],
        "svc": [],
        "app": [],
    },
    "svc": {"short": "SVC"},
    "stacks": {"nativeha-rhel": {"mechanism": "native-ha", "short": "NHAR", "groups": []}},
}


def _exprs(panels: list[dict]) -> list[str]:
    return [t["expr"] for p in panels for t in p.get("targets", []) if "expr" in t]


def _stripe_titles(panels: list[dict]) -> list[str]:
    # a status-stripe tile is the background-coloured stat that opens each row
    return [
        p["title"]
        for p in panels
        if p.get("type") == "stat" and p.get("options", {}).get("colorMode") == "background"
    ]


def test_uid_is_pinned():
    assert DASHBOARD_UID == "lab-watcher"
    assert build_watcher(FIXTURE)["uid"] == "lab-watcher"


def test_a_support_row_per_support_host_in_curated_order():
    d = build_watcher(FIXTURE)
    # every commons support host has a status-stripe (its row), in the curated order
    support = [p for p in d["panels"] if p["type"] == "stat"]
    stripes = _stripe_titles(d["panels"])
    for host in SUPPORT_HOSTS:
        assert host in stripes, f"no support row for {host}"
    # the support hosts appear before the stack rows, in curated order
    assert stripes[: len(SUPPORT_HOSTS)] == SUPPORT_HOSTS
    assert support  # sanity


def test_support_row_carries_all_instrument_columns():
    d = build_watcher(FIXTURE)
    # the obs host's row: up stripe · service pill · CPU · mem · uptime (all host-scoped)
    blob = " ".join(e for e in _exprs(d["panels"]) if 'host="obs"' in e)
    assert 'up{job="node",host="obs"}' in blob  # status stripe = node liveness
    assert "node_systemd_unit_state" in blob  # defining-service pill
    assert "node_cpu_seconds_total" in blob  # CPU busy
    assert "node_memory_MemAvailable_bytes" in blob and "node_memory_MemTotal_bytes" in blob
    # uptime is time-since-boot (the node_time_seconds/node_boot_time_seconds pair)
    assert "node_time_seconds" in blob and "node_boot_time_seconds" in blob


def test_defining_service_pill_is_role_specific():
    d = build_watcher(FIXTURE)
    blob = json.dumps(d["panels"])
    # DNS hosts watch named/bind9; obs watches prometheus+grafana; probe/svc watch the
    # exporter units; app watches the requester unit — all via the systemd collector.
    assert "named.service" in blob and "bind9.service" in blob
    assert "prometheus.service" in blob and "grafana-server.service" in blob
    assert "mq-app-requester" in blob


def test_domain_metric_is_the_roles_one_signal():
    d = build_watcher(FIXTURE)
    exprs = " ".join(_exprs(d["panels"]))
    assert "count(up == 1)" in exprs  # obs scrape targets N/M
    assert 'up{job="ibmmq"}' in exprs  # mon-probe exporters up
    # svc-sim reports the SVC QM's *health* (running/stopped), not a meaningless queue
    # depth — the shared qmgr-status signal, QM name short-derived, object-driven sentinel.
    assert 'ibmmq_qmgr_status{qmgr="SVCQM"}' in exprs
    assert "ibmmq_queue_depth" not in exprs  # the dubious depth stat is gone (#502)
    assert "rate(app_roundtrip_total[1m])" in exprs  # app-client round-trip
    assert "lab_dns_queries_total" in exprs  # DNS q/s (future bind exporter, object-driven)


def test_no_hardcoded_host_literals_hosts_come_from_groups():
    # rename a support host in the topology → the board follows it (nothing hardcoded)
    topo = json.loads(json.dumps(FIXTURE))
    topo["groups"]["obs_box"] = ["obs-renamed"]
    d = build_watcher(topo)
    stripes = _stripe_titles(d["panels"])
    assert "obs-renamed" in stripes and "obs" not in stripes


def test_a_stack_rollup_row_per_stack():
    d = build_watcher(FIXTURE)
    stripes = _stripe_titles(d["panels"])
    # the stack rows follow the support rows; one per stack, keyed by stack name
    assert "pcmk-ubuntu" in stripes and "rdqm-rhel" in stripes


def test_stack_owner_resource_is_mechanism_correct():
    d = build_watcher(FIXTURE)
    exprs = " ".join(_exprs(d["panels"]))
    # pacemaker's QM owner is the pacemaker resource id (mq_qm), NOT the QM name; the
    # other mechanisms own by the short-derived QM name (#351).
    assert 'cluster_resource_owner{resource="mq_qm"' in exprs
    assert 'cluster_resource_owner{resource="RDQMAPP"' in exprs


def test_stack_shows_both_sites_with_san_folded_in():
    d = build_watcher(FIXTURE)
    blob = json.dumps(d["panels"])
    # both sites are shown (live + DR), and the pcmk SAN groups fold into the site node
    # sets (san_a with pcmk_a on Site A, san_b with pcmk_b on Site B).
    assert "san_a|pcmk_a" in blob or "pcmk_a" in blob and "san_a" in blob
    assert "cluster_node_online" in blob  # per-site node health rollup


def test_stack_flow_and_drill_link():
    d = build_watcher(FIXTURE)
    # flow = the app round-trip rate; drill ↗ links to each stack's existing cockpit uid
    exprs = " ".join(_exprs(d["panels"]))
    assert "sum(rate(app_roundtrip_total[1m]))" in exprs
    links = [ln["url"] for p in d["panels"] for ln in p.get("links", [])]
    assert "/d/lab-pcmk-cluster" in links  # pcmk cockpit
    assert "/d/lab-rdqm-cluster" in links  # rdqm cockpit


def test_object_driven_absence_never_reads_healthy():
    # every stripe/service/owner tile carries the STALE special mapping: a missing signal
    # is a coloured absence, the row stays put (the dashboard.py convention).
    d = build_watcher(FIXTURE)
    stale_tiles = [
        p
        for p in d["panels"]
        if p.get("type") == "stat"
        and any(
            m.get("type") == "special"
            for m in p.get("fieldConfig", {}).get("defaults", {}).get("mappings", [])
        )
    ]
    assert stale_tiles, "no tile carries the STALE no-data mapping"
    # the status stripe reads `up` (present as 0=DOWN for a scrape target that is down),
    # so the row never vanishes.
    assert any('up{job="node"' in t["expr"] for p in stale_tiles for t in p.get("targets", []))


def test_section_headers_and_banner_present():
    d = build_watcher(FIXTURE)
    assert any(p["type"] == "text" for p in d["panels"])  # title banner
    row_titles = [p["title"] for p in d["panels"] if p["type"] == "row"]
    assert any(t.startswith("①") for t in row_titles)  # ① Support
    assert any(t.startswith("②") for t in row_titles)  # ② Stacks


def test_empty_topology_still_renders_uid_and_headers():
    d = build_watcher(EMPTY)
    assert d["uid"] == "lab-watcher"
    # no support/stack rows, but the board (banner + section headers) still renders
    assert _stripe_titles(d["panels"]) == []
    assert any(p["type"] == "row" for p in d["panels"])


def test_edge_empty_support_group_and_groupless_stack():
    d = build_watcher(EDGE)
    stripes = _stripe_titles(d["panels"])
    # the empty obs/probe/svc/app groups yield no support rows; infra still does
    assert "infra-client" in stripes and "infra-svc" in stripes
    assert "obs" not in stripes
    # a stack with no groups still emits its rollup row (object-driven)
    assert "nativeha-rhel" in stripes


def test_dashboard_path_is_under_work_grafana_dashboards():
    p = watcher_dashboard_path()
    assert p.name == "lab-watcher.json"
    assert p.parent.parts[-2:] == ("grafana", "dashboards")


def test_real_topology_renders_a_valid_watcher_board():
    dash = json.loads(lab_watcher_dashboard())
    assert dash["uid"] == "lab-watcher"
    stripes = _stripe_titles(dash["panels"])
    # a support row for every real commons support host …
    for host in SUPPORT_HOSTS:
        assert host in stripes, f"real topology: no support row for {host}"
    # … and a rollup row for every real stack (pcmk / rdqm / nhar / nhau)
    for stack in ("pcmk-ubuntu", "rdqm-rhel", "nativeha-rhel", "nativeha-ubuntu"):
        assert stack in stripes, f"real topology: no stack row for {stack}"


def test_site_node_count_dedups_by_member_before_summing():
    # Regression: the state collector runs on a 5s timer per cluster node and each tick
    # emits the FULL membership, so a bare sum(cluster_node_online{...}) multiplies
    # (a healthy 3-node site reads ~9). The per-site "n" count tile must dedup by the
    # `member` label BEFORE summing — clusterboard.py's proven `max by (member)(...)`
    # idiom — the same way clusterboard's own node-count tiles do.
    d = build_watcher(FIXTURE)
    count_tiles = [
        t for p in d["panels"] if p.get("title") == "n" for t in p.get("targets", []) if "expr" in t
    ]
    assert count_tiles, "no per-site node-count ('n') tile found"
    for t in count_tiles:
        expr = t["expr"]
        assert "sum(" in expr, expr
        assert "cluster_node_online" in expr, expr
        assert "max by (member)" in expr, f"count tile not deduped by member before summing: {expr}"


def test_support_and_stack_column_grids_are_contiguous_and_sum_to_24():
    for cols in (_SUPPORT_COLS, _STACK_COLS):
        cursor = 0
        for x, w in cols.values():
            assert x == cursor, f"column grid gap/overlap: expected x={cursor}, got x={x}"
            cursor += w
        assert cursor == 24, f"column grid does not sum to 24 cols: got {cursor}"


def test_real_topology_every_stack_drill_link_resolves_to_a_cockpit():
    # every real stack row must resolve to a mapped cockpit uid — a "/d/" link with an
    # empty uid (a stack missing from _COCKPIT_UID) is a silent dead link. Each stack row
    # emits exactly one drill (linked) panel, in the same order as the stack registry, so
    # zip the ordered drill-link URLs against the ordered stack names.
    dash = json.loads(lab_watcher_dashboard())
    real_stacks = list(
        yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text()).get("stacks", {})
    )
    drill_urls = [p["links"][0]["url"] for p in dash["panels"] if p.get("links")]
    assert real_stacks, "real topology declares no stacks"
    assert len(drill_urls) == len(real_stacks)
    for stack, url in zip(real_stacks, drill_urls, strict=True):
        assert url and url != "/d/", f"real topology: stack {stack} drill link uid is empty"


def test_uptime_is_informational_never_a_health_colour():
    # Regression (#502): uptime is time-since-boot in SECONDS; with no explicit thresholds
    # Grafana's default (red at >= 80) painted a healthy 2-hour uptime RED on every host.
    # Uptime is informational, so it now carries a deliberate neutral colour — never red.
    d = build_watcher(FIXTURE)
    tiles = _by_title(d["panels"], "uptime")
    assert tiles, "no uptime tile"
    for t in tiles:
        assert _thresholds(t) == _NEUTRAL, t
        assert all(step["color"] != "red" for step in _thresholds(t))


def test_cpu_and_mem_carry_deliberate_utilisation_thresholds():
    # CPU/mem must not depend on Grafana's accidental red-at-80 default either; they get an
    # explicit utilisation ramp (green -> amber 75 -> red 90) (#502).
    d = build_watcher(FIXTURE)
    for title in ("CPU", "mem"):
        tiles = _by_title(d["panels"], title)
        assert tiles, f"no {title} tile"
        for t in tiles:
            assert _thresholds(t) == _UTIL, (title, t)


def test_informational_domain_tiles_are_neutral_not_accidental_red():
    # the count/rate domain tiles (obs scrape, probe exporters, app round-trip, DNS q/s)
    # are informational values — deliberate neutral colour, not the red-at-80 accident.
    d = build_watcher(FIXTURE)
    for title in ("scrape ✓", "exporters", "round-trip", "⟲ q/s"):
        tiles = _by_title(d["panels"], title)
        assert tiles, f"no {title} tile"
        for t in tiles:
            assert _thresholds(t) == _NEUTRAL, (title, t)


def test_svc_domain_is_a_qm_health_pill_not_a_queue_depth():
    # #502: the SVC tile reports whether the counterparty QM is running (shared qmgr-status
    # signal, object-driven `or vector(-1)`), coloured by the running/stopped mappings —
    # NOT a meaningless max-queue-depth number inheriting the red-at-80 accident.
    d = build_watcher(FIXTURE)
    tiles = _by_title(d["panels"], "SVCQM")
    assert tiles, "no SVCQM health tile"
    tile = tiles[0]
    expr = tile["targets"][0]["expr"]
    assert 'ibmmq_qmgr_status{qmgr="SVCQM"}' in expr and "or vector(-1)" in expr
    assert tile["fieldConfig"]["defaults"]["mappings"] == _QMSTATUS_MAP
    # 2 -> Running/green is the healthy mapping; -1 -> the No-status sentinel
    opts = _QMSTATUS_MAP[0]["options"]
    assert opts["2"]["text"] == "Running" and opts["2"]["color"] == "green"
    assert opts["-1"]["text"] == "No status"
