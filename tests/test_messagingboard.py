"""Messaging-layer cockpit coverage (#431): per-stack flow board built from the
#423 MQ metrics + the #429 round-trip signal."""

from __future__ import annotations

import json

from mqlab.messagingboard import messaging_dashboard_path, render_messaging_board


def _exprs(board: dict) -> list[str]:
    return [t["expr"] for p in board["panels"] for t in p.get("targets", []) if "expr" in t]


def test_board_is_per_stack_parameterized_with_no_cross_stack_leak():
    d = render_messaging_board("pcmk-ubuntu", "PCMKAPP", "SVCQM", "PCMK.SVC.REQUEST")
    blob = json.dumps(d)
    assert d["uid"] == "lab-messaging-pcmk-ubuntu"
    assert "messaging" in d["tags"] and "pcmk-ubuntu" in d["tags"]
    assert "PCMKAPP" in blob and "SVCQM" in blob
    assert "PCMKAPP.SVCQM" in blob  # the flow-strip SDR hop is <app>.<svc>
    # a different stack's board never carries this stack's app QM
    assert "PCMKAPP" not in json.dumps(
        render_messaging_board("rdqm-rhel", "RDQMAPP", "SVCQM", "RDQM.SVC.REQUEST")
    )


def test_flow_strip_depth_tile_uses_the_per_stack_request_queue():
    # each board's SVC.REQUEST depth reflects THIS stack's own queue on the shared
    # SVCQM, not a summed shared queue (#446 — the observability 1b buys back).
    exprs = _exprs(render_messaging_board("pcmk-ubuntu", "PCMKAPP", "SVCQM", "PCMK.SVC.REQUEST"))
    assert any('ibmmq_queue_depth{qmgr="SVCQM",queue="PCMK.SVC.REQUEST"}' in e for e in exprs)


def test_status_band_is_flow_indicators_not_depth_sum():
    exprs = _exprs(render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST"))
    assert any("rate(app_roundtrip_total[1m])" in e for e in exprs)  # message rate
    assert any("app_roundtrip_failures_total" in e for e in exprs)  # failure rate + OK%
    assert any('ibmmq_qmgr_status{qmgr="APP"}' in e for e in exprs)  # QM up
    # deliberately NO summed queue depth in the status band
    blob = json.dumps(render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST"))
    assert "sum(ibmmq_queue_depth" not in blob
    # success% must NOT fall back to a fake 100% on no traffic (#440) — idle reads No data
    assert "or vector(100)" not in blob


def test_roundtrip_timeline_uses_the_429_histogram():
    exprs = _exprs(render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST"))
    assert any("app_roundtrip_latency_ms_bucket" in e for e in exprs)
    assert any("histogram_quantile(0.95" in e for e in exprs)


def test_logs_panel_covers_both_app_and_svc_units():
    d = render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST")
    logs = next(p for p in d["panels"] if p["type"] == "logs")
    expr = logs["targets"][0]["expr"]
    # Loki =~ is anchored and the unit label carries the .service suffix, so the unit
    # names MUST be wildcarded — a bare name never matches mq-*.service (#440).
    assert "mq-app-requester.*" in expr and "mq-svc-responder.*" in expr


def test_flow_strip_channel_tiles_use_status_squash():
    exprs = _exprs(render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST"))
    assert any('ibmmq_channel_status_squash{qmgr="APP",channel="APP.SVRCONN"}' in e for e in exprs)
    assert any('ibmmq_channel_status_squash{qmgr="SVC",channel="SVC.SVRCONN"}' in e for e in exprs)


def test_drilldown_seam_links_queue_and_channel_rows():
    blob = json.dumps(render_messaging_board("s", "APP", "SVC", "S.SVC.REQUEST"))
    assert "/d/lab-messaging-queue?var-queue=" in blob
    assert "/d/lab-messaging-channel?var-channel=" in blob


def test_dashboard_path_is_per_stack():
    assert messaging_dashboard_path("pcmk-ubuntu").name == "lab-messaging-pcmk-ubuntu.json"


def _stack_cfg(short, provision):
    return {
        "mechanism": "m",
        "os": "o",
        "short": short,
        "provision": provision,
        "groups": [],
        "qm": {},
    }


def test_write_messaging_dashboards_renders_provisioned_stacks_only(monkeypatch, tmp_path):
    import mqlab.messagingboard as mb
    import mqlab.stacks as stacks_mod

    topo = {
        "nodes": {},
        "groups": {},
        "svc": {"short": "SVC", "conn": "10.60.0.50", "exporter_port": 9158},
        "stacks": {
            "live": _stack_cfg("LIVE", "ansible/x.yml"),
            "reserved": _stack_cfg("RSVD", None),
        },
    }
    monkeypatch.setattr(stacks_mod, "_topology", lambda: topo)
    monkeypatch.setattr(
        mb, "messaging_dashboard_path", lambda name: tmp_path / f"lab-messaging-{name}.json"
    )
    paths = mb.write_messaging_dashboards()
    # the reserved stack (provision: null) is filtered out; only the live one renders
    assert [p.name for p in paths] == ["lab-messaging-live.json"]
    assert "LIVEAPP" in paths[0].read_text() and "SVCQM" in paths[0].read_text()
