"""No silent OpenSearch rejections (#1238): Data Prepper scrape + panel + alert guard.

When OpenSearch rejects a document, Data Prepper's OpenSearch sink logs a WARN and drops
it. #1238 makes that loss visible with three pieces that must agree with each other:

- the prometheus role scrapes Data Prepper's core server (`/metrics/prometheus`) on the
  port the data-prepper role pins in `data-prepper-config.yaml`;
- the `log_pipeline` alerting rules in `lab.rules.yml` fire on the sink's
  `documentErrors` counter, plus a guard that fires if that counter is not scraped at all;
- the Watcher board's ③ Log pipeline section charts the same counters and shows the same
  alerts' firing state.

The metric names come from Data Prepper 2.16.0 source (meter `<pipeline>.<plugin>.<metric>`
→ Prometheus `<pipeline>_<plugin>_<metric>_total`). These tests pin every place that
spells them, so a correction after a live scrape is a single, test-guided change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2
import yaml

from mqlab.watcherboard import (
    DP_ABSENT_ALERT,
    DP_ALERTS,
    DP_BULK_REQUEST_FAILED,
    DP_DOCUMENT_ERRORS,
    DP_DOCUMENTS_SUCCESS,
    DP_JOB,
    DP_METRIC_PREFIX,
    DP_REJECT_ALERT,
    build_watcher,
    lab_watcher_dashboard,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES = REPO_ROOT / "ansible" / "roles"
PROM = ROLES / "prometheus"
DP = ROLES / "data-prepper"


def _defaults(role: Path) -> dict[str, Any]:
    data = yaml.safe_load((role / "defaults" / "main.yml").read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data, f"{role} defaults did not parse"
    return data


def _render(template: Path, context: dict[str, Any]) -> dict[str, Any]:
    """Render an Ansible template with a role-defaults context (StrictUndefined, so a
    variable the defaults do not define fails loud) and parse the YAML result."""
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        # YAML, not HTML: no escaping, or `| to_json` quotes would render as &#34;.
        autoescape=jinja2.select_autoescape(default_for_string=False),
    )
    env.filters["to_json"] = json.dumps  # Ansible's filter, for pipelines.yaml.j2
    rendered = env.from_string(template.read_text(encoding="utf-8")).render(**context)
    doc = yaml.safe_load(rendered)
    assert isinstance(doc, dict), f"{template} did not render to a YAML mapping"
    return doc


def _scrape_job(name: str) -> dict[str, Any]:
    doc = _render(PROM / "templates" / "prometheus.yml.j2", _defaults(PROM))
    jobs = {j["job_name"]: j for j in doc["scrape_configs"]}
    assert name in jobs, f"no {name!r} scrape job in prometheus.yml"
    return jobs[name]


def _log_pipeline_rules() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load((PROM / "files" / "lab.rules.yml").read_text(encoding="utf-8"))
    groups = {g["name"]: g for g in doc["groups"]}
    assert "log_pipeline" in groups, "lab.rules.yml has no log_pipeline group"
    return {r["alert"]: r for r in groups["log_pipeline"]["rules"]}


# --- scrape target --------------------------------------------------------------------


def test_data_prepper_is_scraped_on_its_prometheus_metrics_path() -> None:
    job = _scrape_job(DP_JOB)
    assert job["metrics_path"] == "/metrics/prometheus"
    targets = [t for sc in job["static_configs"] for t in sc["targets"]]
    assert targets == [_defaults(PROM)["prometheus_data_prepper_target"]]


def test_scrape_target_port_matches_the_data_prepper_server_port() -> None:
    """The scrape target and Data Prepper's server_port are set in two roles; a drift
    would leave the target down and the rejection alert blind."""
    target = _defaults(PROM)["prometheus_data_prepper_target"]
    host, _, port = target.rpartition(":")
    assert host == "localhost", "Data Prepper is co-located with Prometheus on obs (#1179)"
    assert int(port) == _defaults(DP)["data_prepper_server_port"]


def test_existing_scrape_jobs_are_untouched() -> None:
    assert _scrape_job("node")["file_sd_configs"]
    assert _scrape_job("ibmmq")["file_sd_configs"]


def test_data_prepper_config_pins_the_metrics_endpoint() -> None:
    doc = _render(DP / "templates" / "data-prepper-config.yaml.j2", _defaults(DP))
    assert doc["ssl"] is False
    assert doc["server_port"] == 4900  # Data Prepper's default, made explicit
    assert doc["metric_registries"] == ["Prometheus"]


# --- metric names ---------------------------------------------------------------------


def test_metric_prefix_derives_from_the_pipeline_and_sink_names() -> None:
    """Data Prepper names plugin metrics `<pipeline>.<plugin>.<metric>`; the Prometheus
    registry turns '.' and '-' into '_'. The prefix must follow pipelines.yaml.j2, so a
    pipeline rename breaks here instead of silently blinding the alert."""
    doc = _render(DP / "templates" / "pipelines.yaml.j2", _defaults(DP))
    assert len(doc) == 1, "expected exactly one pipeline"
    pipeline, body = next(iter(doc.items()))
    sinks = [next(iter(s)) for s in body["sink"]]
    assert "opensearch" in sinks
    expected = f"{pipeline}_opensearch_".replace("-", "_").replace(".", "_")
    assert expected == DP_METRIC_PREFIX


def test_sink_metrics_are_counters_named_with_total() -> None:
    # documentErrors / documentsSuccess / bulkRequestFailed are Micrometer Counters in
    # BulkRetryStrategy (2.16.0), exposed with the `_total` counter suffix.
    for metric, stem in (
        (DP_DOCUMENT_ERRORS, "documentErrors"),
        (DP_DOCUMENTS_SUCCESS, "documentsSuccess"),
        (DP_BULK_REQUEST_FAILED, "bulkRequestFailed"),
    ):
        assert metric == f"{DP_METRIC_PREFIX}{stem}_total"


# --- alert rules ----------------------------------------------------------------------


def test_log_pipeline_alerts_are_exactly_the_board_alerts() -> None:
    assert set(_log_pipeline_rules()) == set(DP_ALERTS)


def test_rejection_alert_fires_on_a_single_rejected_document() -> None:
    rule = _log_pipeline_rules()[DP_REJECT_ALERT]
    expr = rule["expr"]
    assert f'{DP_DOCUMENT_ERRORS}{{job="{DP_JOB}"}}' in expr
    # increase() over a window longer than `for`, compared to zero: one document keeps
    # the increase above zero long enough for the rule to fire.
    assert "increase(" in expr and "[5m]" in expr
    assert expr.strip().endswith("> 0")
    assert rule["for"] == "1m"
    assert rule["annotations"]["summary"] and rule["annotations"]["description"]


def test_absent_guard_fires_when_the_counter_is_not_scraped() -> None:
    rule = _log_pipeline_rules()[DP_ABSENT_ALERT]
    assert rule["expr"].strip() == f'absent({DP_DOCUMENT_ERRORS}{{job="{DP_JOB}"}})'
    assert rule["for"] == "10m"  # covers Data Prepper's slow cold JVM start


def test_recording_rule_is_preserved() -> None:
    doc = yaml.safe_load((PROM / "files" / "lab.rules.yml").read_text(encoding="utf-8"))
    groups = {g["name"]: g for g in doc["groups"]}
    assert [r["record"] for r in groups["lab_network"]["rules"]] == ["lab_network_health"]


# --- Watcher ③ Log pipeline section ---------------------------------------------------

_TOPO: dict[str, Any] = {"groups": {"obs_box": ["obs"]}, "svc": {}, "stacks": {}}


def _section(topo: dict[str, Any]) -> list[dict[str, Any]]:
    panels = build_watcher(topo)["panels"]
    rows = [p for p in panels if p["type"] == "row" and p["title"].startswith("③")]
    if not rows:
        return []
    y = rows[0]["gridPos"]["y"]
    return [p for p in panels if p["type"] != "row" and p["gridPos"]["y"] > y]


def _exprs(panel: dict[str, Any]) -> list[str]:
    return [t["expr"] for t in panel["targets"]]


def test_section_leads_with_a_written_vs_rejected_time_series() -> None:
    section = _section(_TOPO)
    assert section, "no ③ Log pipeline section"
    series = section[0]
    assert series["type"] == "timeseries"
    exprs = _exprs(series)
    for metric in (DP_DOCUMENTS_SUCCESS, DP_DOCUMENT_ERRORS, DP_BULK_REQUEST_FAILED):
        assert f'sum(rate({metric}{{job="{DP_JOB}"}}[1m]))' in exprs
    legends = [t["legendFormat"] for t in series["targets"]]
    assert legends[:2] == ["written", "rejected (dropped)"]
    red = [
        o
        for o in series["fieldConfig"]["overrides"]
        if o["matcher"]["options"] == "rejected (dropped)"
    ]
    assert red and red[0]["properties"][0]["value"]["fixedColor"] == "red"


def test_section_shows_alert_firing_state_and_scrape_health() -> None:
    by_title = {p["title"]: p for p in _section(_TOPO)}
    alert_expr = _exprs(by_title["log-pipeline alerts"])[0]
    for name in DP_ALERTS:
        assert name in alert_expr
    assert 'alertstate="firing"' in alert_expr and alert_expr.endswith("or vector(0)")
    assert _exprs(by_title["Data Prepper metrics scrape"]) == [f'up{{job="{DP_JOB}"}}']


def test_section_panels_fit_the_grid() -> None:
    for p in _section(_TOPO):
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]


def test_section_is_absent_without_an_obs_node() -> None:
    assert _section({"groups": {}, "svc": {}, "stacks": {}}) == []
    assert _section({"groups": {"obs_box": []}, "svc": {}, "stacks": {}}) == []


def test_real_topology_renders_the_log_pipeline_section() -> None:
    dash = json.loads(lab_watcher_dashboard())
    assert any(p["type"] == "row" and p["title"].startswith("③") for p in dash["panels"])
