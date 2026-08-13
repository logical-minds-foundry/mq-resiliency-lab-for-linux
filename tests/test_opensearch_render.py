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
INSTALL_TASKS = OPENSEARCH_ROLE / "tasks" / "install.yml"
CONFIGURE_TASKS = OPENSEARCH_ROLE / "tasks" / "configure.yml"


def _load_tasks(path: Path) -> list[dict]:
    """Parse an Ansible tasks file into a list of task dicts (fail loud if empty)."""
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks, f"{path} did not parse to a non-empty task list"
    return tasks


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


# --- JVM-bootstrap cost cuts (#1034, epic .github#198) --------------------------------


def _heap_dropin_task(tasks: list[dict]) -> dict:
    """Find the JVM heap drop-in copy task in install.yml."""
    for task in tasks:
        copy = task.get("ansible.builtin.copy")
        if isinstance(copy, dict) and str(copy.get("dest", "")).endswith(
            "jvm.options.d/heap.options"
        ):
            return task
    raise AssertionError("install.yml has no JVM heap drop-in copy task (dest heap.options)")


def test_heap_dropin_disables_alwayspretouch() -> None:
    """The heap drop-in must disable AlwaysPreTouch so the JVM does not pre-fault the
    whole heap up front under host memory pressure (#1034) — and must still set the
    heap size (-Xmx)."""
    content = _heap_dropin_task(_load_tasks(INSTALL_TASKS))["ansible.builtin.copy"]["content"]
    assert "-XX:-AlwaysPreTouch" in content, (
        "heap.options drop-in must carry -XX:-AlwaysPreTouch to override the launcher's "
        f"default +AlwaysPreTouch; got: {content!r}"
    )
    assert "-Xmx" in content, f"heap.options drop-in lost its -Xmx heap size; got: {content!r}"


def test_install_drops_performance_analyzer_javaagent() -> None:
    """install.yml must strip the Performance Analyzer -javaagent line from the base
    config/jvm.options (unused by logsearch; heavy class-load instrumentation, #1034)."""
    tasks = _load_tasks(INSTALL_TASKS)
    matches = [
        line
        for task in tasks
        if isinstance((line := task.get("ansible.builtin.lineinfile")), dict)
        and line.get("state") == "absent"
        and "opensearch-agent" in str(line.get("regexp", ""))
    ]
    assert len(matches) == 1, (
        "install.yml must have exactly one lineinfile state=absent task removing the "
        f"opensearch-agent.jar javaagent line; found {len(matches)}"
    )
    lineinfile = matches[0]
    assert str(lineinfile["path"]).endswith("config/jvm.options"), (
        f"perf-analyzer removal must target config/jvm.options; got path {lineinfile.get('path')!r}"
    )
    assert r"agent/opensearch-agent\.jar" in lineinfile["regexp"], (
        f"perf-analyzer removal regexp must match the agent jar line; got {lineinfile['regexp']!r}"
    )


# --- readiness budget widened to 15 min (#1034, epic .github#198) ---------------------


def test_readiness_wait_budget_is_15_minutes() -> None:
    """The cluster-readiness wait must budget ~900 s (180 retries x 5 s delay = 15 min)
    to cover host-oversubscribed cold boots while staying fail-loud (#1034)."""
    tasks = _load_tasks(CONFIGURE_TASKS)
    wait = next(
        (t for t in tasks if str(t.get("name", "")).startswith("wait for the OpenSearch cluster")),
        None,
    )
    assert wait is not None, "configure.yml has no 'wait for the OpenSearch cluster' task"
    assert "ansible.builtin.uri" in wait, "readiness wait task must use ansible.builtin.uri"
    assert wait.get("retries") == 180, f"readiness retries must be 180; got {wait.get('retries')!r}"
    assert wait.get("delay") == 5, f"readiness delay must be 5 s; got {wait.get('delay')!r}"
