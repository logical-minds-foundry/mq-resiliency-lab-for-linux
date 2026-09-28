"""Coexistence-budget guardrails for the consolidated obs node (#1180, epic .github#267).

Epic #267 folds the logsearch tier onto the obs node (4 vCPU / 10 GB), so six
memory-significant services share one box: OpenSearch (JVM), Data Prepper (JVM), and
OpenSearch Dashboards (Node) alongside the Go services (Prometheus/Grafana/Loki + the
mq_prometheus exporter) and the OS. Left unbounded, OpenSearch grabs ~50 % of RAM and
Dashboards' Node old-space is sized from total RAM, so the runtimes would overcommit 10 GB.

These guards pin the explicit heap caps (see
`docs/reports/2026-09-28-obs-coexistence-jvm-budget.md`) so a future edit cannot quietly
drift them at `vrg-validate` time instead of at the slow arm64 cold-rebuild acceptance gate
(VAL #1177): each cap is checked within a per-service band AND wired into its rendered
artifact, the three caps + reserved OS/Go/non-heap overhead are asserted to fit 10 GB with a
margin, and Dashboards' `opensearch.requestTimeout` is asserted at/above the chosen bound
(the #249 migration-deadlock fix). They mirror `tests/test_startup_budgets.py` /
`tests/test_logsearch_budgets.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES = REPO_ROOT / "ansible" / "roles"

OPENSEARCH_DEFAULTS = ROLES / "opensearch" / "defaults" / "main.yml"
OPENSEARCH_INSTALL = ROLES / "opensearch" / "tasks" / "install.yml"
DATA_PREPPER_DEFAULTS = ROLES / "data-prepper" / "defaults" / "main.yml"
DATA_PREPPER_INSTALL = ROLES / "data-prepper" / "tasks" / "install.yml"
DASHBOARDS_DEFAULTS = ROLES / "opensearch-dashboards" / "defaults" / "main.yml"
DASHBOARDS_NODE_OPTIONS = ROLES / "opensearch-dashboards" / "templates" / "node.options.j2"
DASHBOARDS_CONFIG = ROLES / "opensearch-dashboards" / "templates" / "opensearch_dashboards.yml.j2"

# --- the shared budget (spec §5.1 / §5.4; docs/reports/2026-09-28-obs-coexistence-jvm-budget.md).
# The DESIGN target the consolidated obs node grows to; hardcoded (not read from topology)
# because the topology grow-to-10240 lands on a separate branch (Task 2 / #1179) and this
# task must size against the target regardless of merge order.
NODE_MEMORY_MB = 10240
OS_RESERVE_MB = 1024  # OS + kernel + page cache
GO_SERVICES_RESERVE_MB = 1536  # prometheus 512 + grafana 384 + loki 512 + exporter 128
JVM_NONHEAP_RESERVE_MB = 1792  # OpenSearch off-heap ~1024 + Data Prepper ~256 + Node non-heap ~512
MIN_MARGIN_MB = 1024  # headroom the caps must leave beyond the reserved overhead

# Per-service heap bands (MB): wide enough for the chosen figure, tight enough to catch a
# drift up to the unbounded default (OpenSearch ~50 % of RAM) or back to the old standalone value.
OPENSEARCH_HEAP_BAND = (1024, 2560)  # chosen 2048
DATA_PREPPER_HEAP_BAND = (256, 768)  # chosen 512 (was 1024 standalone)
DASHBOARDS_OLDSPACE_BAND = (512, 1536)  # chosen 1024

# Dashboards' opensearch.requestTimeout must clear this (ms) — the default 30 s fired mid
# saved-objects migration under nested virt and deadlocked `.kibana_1` (#249).
MIN_REQUEST_TIMEOUT_MS = 120000


def _defaults(path: Path) -> dict:
    """Parse an Ansible role defaults file into a dict (fail loud if it is not a mapping)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and data, f"{path} did not parse to a non-empty mapping"
    return data


def _heap_mb(value: str | int) -> int:
    """Parse a JVM/Node heap size (`2g`, `512m`, or a bare MB integer) into megabytes.

    Fail loud on an unrecognized unit rather than silently coercing — a mis-parsed cap would
    make the budget assertion meaningless.
    """
    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+)([gm]?)", text)
    assert match is not None, f"unrecognized heap size {value!r} (expected e.g. 2g / 512m / 1024)"
    number, unit = int(match.group(1)), match.group(2)
    if unit == "g":
        return number * 1024
    if unit == "m":
        return number
    return number  # bare integer => already MB


def _opensearch_heap_mb() -> int:
    return _heap_mb(_defaults(OPENSEARCH_DEFAULTS)["opensearch_heap"])


def _data_prepper_heap_mb() -> int:
    return _heap_mb(_defaults(DATA_PREPPER_DEFAULTS)["data_prepper_heap"])


def _dashboards_oldspace_mb() -> int:
    value = _defaults(DASHBOARDS_DEFAULTS)["opensearch_dashboards_max_old_space_mb"]
    return _heap_mb(value)


# --- individual caps: set, in band, and wired into the rendered artifact -----------------


def test_opensearch_heap_capped_and_wired() -> None:
    """OpenSearch heap is explicitly capped (no ~50 %-of-RAM default) and flows into the
    jvm.options.d drop-in for both -Xms and -Xmx (#1180)."""
    heap = _opensearch_heap_mb()
    low, high = OPENSEARCH_HEAP_BAND
    assert low <= heap <= high, (
        f"opensearch_heap must be in [{low}, {high}] MB (explicit coexistence cap, not the "
        f"~50 %-of-RAM default); got {heap} MB"
    )
    install = OPENSEARCH_INSTALL.read_text(encoding="utf-8")
    assert "-Xms{{ opensearch_heap }}" in install and "-Xmx{{ opensearch_heap }}" in install, (
        "opensearch install.yml must render both -Xms{{ opensearch_heap }} and "
        "-Xmx{{ opensearch_heap }} into the jvm.options.d drop-in so the cap actually applies"
    )


def test_data_prepper_heap_capped_and_wired() -> None:
    """Data Prepper heap is capped down for coexistence (from the old standalone 1 g) and
    flows into the systemd unit's JAVA_OPTS for both -Xms and -Xmx (#1180)."""
    heap = _data_prepper_heap_mb()
    low, high = DATA_PREPPER_HEAP_BAND
    assert low <= heap <= high, (
        f"data_prepper_heap must be in [{low}, {high}] MB (coexistence cap, down from the old "
        f"standalone 1024 MB); got {heap} MB"
    )
    install = DATA_PREPPER_INSTALL.read_text(encoding="utf-8")
    assert "-Xms{{ data_prepper_heap }}" in install and "-Xmx{{ data_prepper_heap }}" in install, (
        "data-prepper install.yml must render both -Xms{{ data_prepper_heap }} and "
        "-Xmx{{ data_prepper_heap }} into the unit JAVA_OPTS so the cap actually applies"
    )


def test_dashboards_node_heap_capped_and_wired() -> None:
    """Dashboards' Node old-space heap is explicitly capped (no total-RAM sizing) and flows
    into config/node.options via --max-old-space-size (#1180)."""
    oldspace = _dashboards_oldspace_mb()
    low, high = DASHBOARDS_OLDSPACE_BAND
    assert low <= oldspace <= high, (
        f"opensearch_dashboards_max_old_space_mb must be in [{low}, {high}] MB (explicit cap, "
        f"not Node's total-RAM sizing); got {oldspace} MB"
    )
    node_options = DASHBOARDS_NODE_OPTIONS.read_text(encoding="utf-8")
    assert "--max-old-space-size={{ opensearch_dashboards_max_old_space_mb }}" in node_options, (
        "node.options.j2 must render --max-old-space-size from "
        "opensearch_dashboards_max_old_space_mb so the Node old-space cap actually applies"
    )


# --- the combined budget -----------------------------------------------------------------


def test_combined_heap_budget_fits_10gb_with_margin() -> None:
    """The three explicit heap caps + reserved OS/Go/JVM-non-heap overhead must fit the 10 GB
    obs node with at least MIN_MARGIN_MB to spare (spec §5.4). Catches a drift where any one
    cap creeps up until the six services no longer coexist."""
    heaps = _opensearch_heap_mb() + _data_prepper_heap_mb() + _dashboards_oldspace_mb()
    projected = heaps + OS_RESERVE_MB + GO_SERVICES_RESERVE_MB + JVM_NONHEAP_RESERVE_MB
    ceiling = NODE_MEMORY_MB - MIN_MARGIN_MB
    assert projected <= ceiling, (
        f"projected obs-node footprint {projected} MB (heaps {heaps} + OS {OS_RESERVE_MB} + "
        f"Go {GO_SERVICES_RESERVE_MB} + JVM/Node non-heap {JVM_NONHEAP_RESERVE_MB}) must leave "
        f"a >= {MIN_MARGIN_MB} MB margin under the {NODE_MEMORY_MB} MB node (ceiling {ceiling} MB)"
    )


# --- the #249 Dashboards migration/timeout fix -------------------------------------------


def test_dashboards_request_timeout_raised_and_wired() -> None:
    """Dashboards' opensearch.requestTimeout is raised to/above the nested-virt bound and
    rendered into the config so the saved-objects migration cannot time out and deadlock
    `.kibana_1` (#249). The value comes from the role default and flows to the template."""
    timeout = _defaults(DASHBOARDS_DEFAULTS)["opensearch_dashboards_request_timeout_ms"]
    assert isinstance(timeout, int) and timeout >= MIN_REQUEST_TIMEOUT_MS, (
        f"opensearch_dashboards_request_timeout_ms must be an int >= {MIN_REQUEST_TIMEOUT_MS} ms "
        f"(the default 30000 ms fired mid-migration, #249); got {timeout!r}"
    )
    config = DASHBOARDS_CONFIG.read_text(encoding="utf-8")
    assert "opensearch.requestTimeout: {{ opensearch_dashboards_request_timeout_ms }}" in config, (
        "opensearch_dashboards.yml.j2 must render opensearch.requestTimeout from the raised "
        "default so the bump actually reaches Dashboards"
    )


def test_dashboards_config_uses_no_nonexistent_migration_key() -> None:
    """OpenSearch Dashboards (a Kibana 7.10.2 fork) has NO migrations.retryAttempts key and
    fatally rejects unknown config keys — so writing it would BREAK startup, the opposite of
    the fix. Guard against a well-meaning re-introduction; the real migration budget knob is
    migrations.scrollDuration (#1180)."""
    config = DASHBOARDS_CONFIG.read_text(encoding="utf-8")
    # Match an ACTIVE config line (not a comment mentioning the key), so the guard is about
    # what OSD would actually parse — a `# ...retryAttempts...` note is allowed.
    active_retry_attempts = re.search(r"^\s*migrations\.retryAttempts\s*:", config, re.MULTILINE)
    assert active_retry_attempts is None, (
        "opensearch_dashboards.yml.j2 must not SET migrations.retryAttempts — OSD has no such "
        "key and rejects unknown keys (it would fail to start). Use migrations.scrollDuration"
    )
    assert "migrations.scrollDuration:" in config, (
        "opensearch_dashboards.yml.j2 must widen migrations.scrollDuration (the real OSD "
        "migration budget knob) for the nested-virt migration (#1180)"
    )
