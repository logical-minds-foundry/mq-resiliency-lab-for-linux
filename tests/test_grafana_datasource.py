"""Grafana datasource-provisioning render guard (#1025, epic .github#198).

`test_templates_render.py` only *parses* every Ansible `.j2` (a syntax floor). This
guard goes further for the one template whose content is load-bearing for the
log-search tier: it **renders** `roles/grafana/templates/datasource.yml.j2` with a
representative `logsearch_mgmt_ip`, parses the YAML, and asserts the OpenSearch
datasource is wired exactly as #169 Wave 1b will reference it.

Why this matters concretely: the OpenSearch datasource's `uid` is a pinned contract
(`opensearch`) that dashboards reference by `{type: elasticsearch, uid: opensearch}`;
if it drifts, or the URL loses the mgmt IP / `:9200`, or `OpenSearch` falls out of
`deleteDatasources` (so a re-provision can't recreate it with the pinned uid — the
#279 idempotence pattern), the tier silently fails to render its logs. The parse-only
guard would not catch any of those.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "ansible" / "roles" / "grafana" / "templates" / "datasource.yml.j2"

# A representative logsearch mgmt IP. Matches the role default / topology static NIC,
# but the test asserts the rendered URL against THIS value so it proves the variable is
# actually interpolated (not that it happens to equal a hardcoded string).
_MGMT_IP = "10.50.0.4"


def _render() -> dict:
    """Render the datasource template with a representative context and parse the YAML."""
    env = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        autoescape=True,  # render-time no-op here (no HTML); satisfies scanners
    )
    template = env.from_string(TEMPLATE.read_text(encoding="utf-8"))
    rendered = template.render(logsearch_mgmt_ip=_MGMT_IP)
    return yaml.safe_load(rendered)


def _by_name(entries: list[dict]) -> dict[str, dict]:
    return {e["name"]: e for e in entries}


def test_opensearch_datasource_is_pinned_and_plaintext() -> None:
    """The OpenSearch datasource renders with the pinned uid, the built-in
    elasticsearch type, and a plaintext mgmt-plane `:9200` URL carrying the IP."""
    doc = _render()
    datasources = _by_name(doc["datasources"])

    assert "OpenSearch" in datasources, "the OpenSearch datasource must be provisioned"
    os_ds = datasources["OpenSearch"]

    assert os_ds["uid"] == "opensearch", "uid must be pinned to `opensearch` (#169 contract)"
    assert os_ds["type"] == "elasticsearch", (
        "must use the built-in elasticsearch type (no plugin) — OpenSearch is ES-API compatible"
    )
    assert os_ds["access"] == "proxy"
    assert os_ds["url"] == f"http://{_MGMT_IP}:9200", (
        "URL must interpolate the logsearch mgmt IP and target plaintext :9200"
    )
    # Plaintext posture (#827): no TLS, no credentials wired into the datasource.
    assert "basicAuth" not in os_ds
    assert "secureJsonData" not in os_ds
    assert os_ds.get("jsonData", {}).get("index") == "logs-*"
    assert os_ds.get("jsonData", {}).get("timeField") == "@timestamp"


def test_opensearch_in_delete_datasources() -> None:
    """`OpenSearch` must be in deleteDatasources so a re-provision recreates it with the
    pinned uid — Grafana refuses to change a provisioned datasource's uid in place
    (the #279 idempotence pattern that already covers Prometheus/Loki)."""
    doc = _render()
    delete_names = {e["name"] for e in doc["deleteDatasources"]}
    assert "OpenSearch" in delete_names


def test_existing_datasources_untouched() -> None:
    """Regression: the Prometheus/Loki datasources keep their pinned uids and
    localhost URLs — the OpenSearch addition must not disturb them."""
    doc = _render()
    datasources = _by_name(doc["datasources"])

    assert datasources["Prometheus"]["uid"] == "prometheus"
    assert datasources["Prometheus"]["url"] == "http://localhost:9090"
    assert datasources["Loki"]["uid"] == "loki"
    assert datasources["Loki"]["url"] == "http://localhost:3100"
