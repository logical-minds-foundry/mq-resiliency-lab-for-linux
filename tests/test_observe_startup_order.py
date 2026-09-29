"""Guard the observe-phase startup ORDER on the obs box (#1194, epics .github#267/#249).

VAL #1177 measured CPU-contention starving OpenSearch's JVM cold-start: the metrics Go
services (prometheus/grafana/loki/alloy) were started first and their startup burst pegged
all cores, so OpenSearch — which came last — got ~37% of one core and never reached green
(load ~27 on 8 cores; memory fine). The fix serializes the `obs_box` play in site-obs.yml:
the heavy log-tier JVM/Node roles (opensearch -> data-prepper -> opensearch-dashboards, each
already gated to ready) run FIRST on the idle node, THEN the light metrics roles.

This guard fails loudly if that order ever regresses (e.g. a refactor moves a metrics role
ahead of a log-tier role), which would re-introduce the starvation.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_OBS = REPO_ROOT / "ansible" / "site-obs.yml"

# Heavy log-tier cold-starts that must come first, and the light metrics roles that follow.
LOG_TIER = ["opensearch", "data-prepper", "opensearch-dashboards"]
METRICS_TIER = ["loki", "alloy", "prometheus", "grafana-image-renderer", "grafana"]


def _obs_box_role_order() -> list[str]:
    """The include_role names, in order, of the `hosts: obs_box` play in site-obs.yml."""
    plays = yaml.safe_load(SITE_OBS.read_text(encoding="utf-8"))
    play = next((p for p in plays if p.get("hosts") == "obs_box"), None)
    assert play is not None, "site-obs.yml must have a `hosts: obs_box` play"
    order: list[str] = []
    for task in play.get("tasks", []) or []:
        spec = task.get("ansible.builtin.include_role") or task.get("include_role")
        if isinstance(spec, dict) and spec.get("name"):
            order.append(spec["name"])
    return order


def test_log_tier_configures_before_metrics_tier() -> None:
    order = _obs_box_role_order()
    present_log = [r for r in LOG_TIER if r in order]
    present_metrics = [r for r in METRICS_TIER if r in order]
    assert present_log, f"obs_box play must configure the log tier; roles seen: {order}"
    assert present_metrics, f"obs_box play must configure the metrics tier; roles seen: {order}"
    last_log = max(order.index(r) for r in present_log)
    first_metrics = min(order.index(r) for r in present_metrics)
    assert last_log < first_metrics, (
        "observe startup order regressed (#1194): every log-tier role "
        f"{present_log} must be configured BEFORE every metrics-tier role {present_metrics} "
        f"so the heavy JVM cold-starts aren't starved by the metrics burst. Order was: {order}"
    )


def test_opensearch_is_first_of_the_log_tier() -> None:
    # OpenSearch is the store data-prepper + dashboards depend on, and the heaviest cold-start;
    # it must lead so it greens on the idle node before anything else competes.
    order = _obs_box_role_order()
    log_positions = {r: order.index(r) for r in LOG_TIER if r in order}
    assert log_positions.get("opensearch") == min(log_positions.values()), (
        f"opensearch must be the first log-tier role configured (#1194); order: {order}"
    )
