"""Render guard for the opensearch role's `opensearch.yml.j2` (epic .github#198).

`test_templates_render.py` only *parses* every `.j2` (a false-positive-free syntax
floor). This guard goes one step further for the one template whose *content* is
load-bearing for cold-boot: it renders `opensearch.yml.j2` with the role defaults
and asserts the min-distribution invariants.

Why this exists: the min (core-only) OpenSearch distribution ships WITHOUT the
security plugin. Any `plugins.security.*` line left in `opensearch.yml` therefore
becomes an UNKNOWN setting and OpenSearch refuses to start — a defect that only
surfaces at the (slow, expensive) cold-rebuild acceptance gate #1022. This test
catches a regressed `plugins.security` line at `vrg-validate` time instead, and
guards the core single-node settings that must survive alongside it.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENSEARCH_ROLE = REPO_ROOT / "ansible" / "roles" / "opensearch"
TEMPLATE = OPENSEARCH_ROLE / "templates" / "opensearch.yml.j2"
DEFAULTS = OPENSEARCH_ROLE / "defaults" / "main.yml"


def _render_opensearch_yml() -> str:
    """Render `opensearch.yml.j2` with the role defaults as its variable context.

    Every variable the template references (`opensearch_cluster_name`,
    `opensearch_node_name`, `opensearch_network_host`, `opensearch_http_port`,
    `opensearch_data_dir`, `opensearch_repo_dir`, `opensearch_snapshot_repo`) is a
    plain literal in `defaults/main.yml`, so loading the defaults as YAML supplies a
    faithful context with no fact/lookup resolution required. `StrictUndefined` makes
    a template that references a variable the defaults do not define fail loud rather
    than render an empty string.
    """
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    env = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    return env.from_string(TEMPLATE.read_text(encoding="utf-8")).render(**defaults)


def test_rendered_opensearch_yml_has_no_security_plugin_setting() -> None:
    """The rendered config must carry ZERO `plugins.security` settings: the min
    distribution has no security plugin to register them, so any such line is an
    unknown setting that aborts OpenSearch startup (epic .github#198)."""
    rendered = _render_opensearch_yml()
    offending = [
        line
        for line in rendered.splitlines()
        # a *setting* line, not a comment explaining why it is absent
        if "plugins.security" in line and not line.lstrip().startswith("#")
    ]
    assert not offending, (
        "rendered opensearch.yml carries a plugins.security setting, which the min "
        f"(core-only) distribution refuses to start on: {offending}"
    )


def test_rendered_opensearch_yml_keeps_single_node_discovery() -> None:
    """`discovery.type: single-node` is load-bearing for the one-node logsearch tier
    and must survive the min switch."""
    rendered = _render_opensearch_yml()
    assert "discovery.type: single-node" in rendered


def test_rendered_opensearch_yml_keeps_http_port() -> None:
    """The plaintext `http.port` binding (9200) is what Data Prepper and Dashboards
    target — it must survive the min switch."""
    rendered = _render_opensearch_yml()
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert f"http.port: {defaults['opensearch_http_port']}" in rendered
