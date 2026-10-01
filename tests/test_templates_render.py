"""Template-render guard (#774, epic .github#104): every Ansible `.j2` must parse.

`vrg-validate` lints the *text* of Jinja templates (shellcheck on `*.sh.j2`,
ansible-lint, etc.) but never asks Jinja to **render** them. A whole class of
defect therefore passes CI green and only surfaces at deploy time (or, worse, at
a cold-rebuild acceptance gate — the slowest, most expensive place to catch it):

- malformed or unbalanced `{{ ... }}` / `{% ... %}` expressions,
- invalid characters inside an expression,
- unclosed blocks.

The concrete bite (epic .github#122, task #772 / deploy #763): `run.sh.j2`
carried a shellcheck-disable *comment* containing a literal `{{ … }}` with a
Unicode ellipsis. Jinja parses the double-brace as an expression and the ellipsis
is an invalid char, so rendering blew up (`unexpected char '…'`) on every node —
yet `vrg-validate` was green because it never rendered the template.

This guard closes that gap at the cheapest possible place: a Jinja **parse** of
every Ansible template. Parsing needs no variable values and resolves no filters
(Jinja resolves those at render time), so it produces **zero false positives**
from Ansible-specific filters/lookups/facts while still catching every
syntax/malformed-expression defect of the class above — exactly the failure mode
#774 was filed for.

Scope note (deliberately repo-local): the *ideal* home is an upstream
`template-render` check in `vrg-validate` itself (vergil-tooling), since the gap
is generic to any Vergil-managed repo with Jinja templates. A full render under
`StrictUndefined` — which would additionally catch undefined-variable and typo
bugs — needs each template's representative variable context (role defaults +
group_vars + host_vars + facts), which is a per-template fixture surface better
owned upstream. This guard is the robust, false-positive-free repo-local floor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPO_ROOT / "ansible"

DATA_PREPPER_INSTALL = ANSIBLE_ROOT / "roles" / "data-prepper" / "tasks" / "install.yml"
ALLOY_CONFIG = ANSIBLE_ROOT / "roles" / "alloy" / "templates" / "config.alloy.j2"


def _ansible_jinja_env() -> jinja2.Environment:
    """A Jinja environment whose whitespace/delimiter settings mirror Ansible's.

    Ansible uses the default Jinja delimiters (`{{ }}`, `{% %}`, `{# #}`) with
    `trim_blocks` on. Parsing is delimiter/whitespace-driven, so matching these
    keeps the parse faithful to what Ansible does at deploy time. `parse()` never
    resolves filters or variables, so no Ansible plugin registration is required.
    """
    # autoescape is a RENDER-time setting; this env only ever calls `.parse()`
    # (an AST build, driven purely by delimiters/whitespace), so autoescape has
    # zero effect on the result. It is set True to satisfy security scanners
    # (CodeQL py/jinja2/autoescape-false) without changing any parse behaviour —
    # not because these config/shell/service templates emit HTML.
    return jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        autoescape=True,
    )


def _templates() -> list[Path]:
    return sorted(ANSIBLE_ROOT.rglob("*.j2"))


def test_ansible_templates_are_discovered() -> None:
    """Guard the guard: a discovery/glob regression must not silently pass by
    finding zero templates. The repo has ~33 Ansible `.j2` files."""
    templates = _templates()
    assert len(templates) >= 20, (
        f"expected to discover the Ansible .j2 templates under {ANSIBLE_ROOT}, "
        f"found only {len(templates)} — has the template layout moved?"
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_ansible_template_parses(template: Path) -> None:
    """Every Ansible `.j2` template must parse as valid Jinja (#774)."""
    env = _ansible_jinja_env()
    source = template.read_text(encoding="utf-8")
    try:
        env.parse(source)
    except jinja2.TemplateSyntaxError as exc:
        rel = template.relative_to(REPO_ROOT)
        pytest.fail(
            f"Jinja template fails to parse and would break at deploy/render time: "
            f"{rel}: {exc.message} (line {exc.lineno})"
        )


def _data_prepper_unit_content() -> str:
    """Extract the inline systemd-unit `content:` block that `install.yml`'s copy
    task drops at `/etc/systemd/system/data-prepper.service`.

    The unit is authored inline (not a `.j2`), so the generic parse guard above
    never touches it. This pulls the exact Jinja source that Ansible renders.
    """
    tasks = yaml.safe_load(DATA_PREPPER_INSTALL.read_text(encoding="utf-8"))
    for task in tasks:
        copy = task.get("ansible.builtin.copy", {})
        if copy.get("dest") == "/etc/systemd/system/data-prepper.service":
            return copy["content"]
    raise AssertionError(
        "data-prepper install.yml no longer has a copy task rendering "
        "/etc/systemd/system/data-prepper.service — has the unit moved?"
    )


def test_data_prepper_unit_environment_line_is_single_quoted() -> None:
    """The systemd unit's JVM heap flags must both survive (#1024).

    Rendered *unquoted*, `Environment=JAVA_OPTS=-Xms1g -Xmx1g` makes systemd split
    on the space into `JAVA_OPTS=-Xms1g` plus a bogus bare `-Xmx1g`
    ("Invalid environment assignment, ignoring: -Xmx1g") — so `-Xmx` is silently
    dropped and the heap is uncapped. The value must be a single quoted
    assignment: `Environment="JAVA_OPTS=…"`.
    """
    env = _ansible_jinja_env()
    context = {
        "data_prepper_user": "data-prepper",
        "data_prepper_home": "/usr/share/data-prepper",
        "data_prepper_data_dir": "/var/lib/data-prepper",
        "data_prepper_heap": "1g",
    }
    rendered = env.from_string(_data_prepper_unit_content()).render(context)

    env_lines = [ln.strip() for ln in rendered.splitlines() if ln.strip().startswith("Environment")]
    assert env_lines == ['Environment="JAVA_OPTS=-Xms1g -Xmx1g"'], (
        "the data-prepper Environment= line must be a single quoted assignment so "
        f"systemd keeps -Xmx; got {env_lines!r}"
    )
    # The quoted form is the whole point: an unquoted `Environment=JAVA_OPTS=` is
    # exactly the regression (systemd would split it and drop -Xmx).
    assert 'Environment="JAVA_OPTS=' in rendered
    assert "Environment=JAVA_OPTS=" not in rendered
    # -Xmx must appear only inside the quoted value, never as a bare token.
    assert rendered.count("-Xmx") == 1


DATA_PREPPER_ROLE = ANSIBLE_ROOT / "roles" / "data-prepper"
DATA_PREPPER_TEMPLATES = DATA_PREPPER_ROLE / "templates"
DATA_PREPPER_PIPELINES = DATA_PREPPER_TEMPLATES / "pipelines.yaml.j2"
DATA_PREPPER_DEFAULTS = DATA_PREPPER_ROLE / "defaults" / "main.yml"
DATA_PREPPER_CONFIGURE = DATA_PREPPER_ROLE / "tasks" / "configure.yml"


def _data_prepper_context() -> dict:
    """The data-prepper role defaults, with their `{{ other_default }}` references resolved.

    Rendering from the real defaults (not a hand-copied context) means the tests pin what
    the role actually ships. Only `data_prepper_arch` needs an Ansible fact; it and the two
    keys derived from it are dropped (no template under test uses them).
    """
    raw = yaml.safe_load(DATA_PREPPER_DEFAULTS.read_text(encoding="utf-8"))
    fact_derived = ("data_prepper_arch", "data_prepper_pkg", "data_prepper_url")
    raw = {k: v for k, v in raw.items() if k not in fact_derived}
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=True)
    context = dict(raw)
    # Defaults reference each other a few levels deep (dlq_file -> dlq_dir -> data_dir);
    # re-render until stable.
    for _ in range(5):
        context = {
            k: env.from_string(v).render(context) if isinstance(v, str) and "{{" in v else v
            for k, v in context.items()
        }
    assert not any(isinstance(v, str) and "{{" in v for v in context.values())
    return context


def _render_data_prepper_template(name: str) -> str:
    env = _ansible_jinja_env()
    # The one Ansible filter these templates use. Alias Jinja's built-in `tojson`: it is
    # autoescape-safe (the shared env autoescapes) and still emits valid JSON.
    env.filters["to_json"] = env.filters["tojson"]
    source = (DATA_PREPPER_TEMPLATES / name).read_text(encoding="utf-8")
    return env.from_string(source).render(_data_prepper_context())


def _data_prepper_pipeline() -> dict:
    return yaml.safe_load(_render_data_prepper_template("pipelines.yaml.j2"))["logs-pipeline"]


def test_data_prepper_parse_json_is_gated_to_json_looking_bodies() -> None:
    """parse_json must only run on bodies that start with `{` (#1220).

    Ungated, every plain-text journal line (systemd unit messages, cron/pam,
    OpenSearch's `[timestamp][LEVEL]` lines) hit the invalid-JSON path and
    logged an ERROR per event (~447/min on obs). The gate skips those. Anything
    that looks like JSON but still fails to parse is tagged and stays loud.
    """
    pipeline = _data_prepper_pipeline()
    parse_json = next(p["parse_json"] for p in pipeline["processor"] if "parse_json" in p)
    assert parse_json == {
        "source": "body",
        "parse_when": 'startsWith(/body, "{")',
        "tags_on_failure": ["_jsonparsefailure"],
        "handle_failed_events": "skip",
    }


DLQ_FILE = "/var/lib/data-prepper/dlq/opensearch-sink.dlq"


def test_data_prepper_opensearch_sink_writes_rejects_to_local_dlq_file() -> None:
    """Rejected documents go to a local DLQ file on obs, not just a WARN line (#1239).

    `dlq_file` and the S3 `dlq` are mutually exclusive in 2.16.0
    (OpenSearchSinkConfig.isDlqValid); the lab has no S3, so only `dlq_file`.
    """
    sinks = _data_prepper_pipeline()["sink"]
    opensearch = next(s["opensearch"] for s in sinks if "opensearch" in s)
    assert opensearch["dlq_file"] == DLQ_FILE
    assert "dlq" not in opensearch

    # DP fails the sink at init if it cannot open the file, so the install half must
    # create the dir, owned by the service user with an explicit, non-group-writable mode.
    install = yaml.safe_load(DATA_PREPPER_INSTALL.read_text(encoding="utf-8"))
    dirs = [t["ansible.builtin.file"] for t in install if "ansible.builtin.file" in t]
    assert {
        "path": "{{ data_prepper_dlq_dir }}",
        "state": "directory",
        "owner": "{{ data_prepper_user }}",
        "group": "{{ data_prepper_user }}",
        "mode": "0750",
    } in dirs


def _logrotate_rule() -> tuple[str, list[str]]:
    """Parse the rendered DLQ logrotate config into (path, directive lines)."""
    lines = [
        line.strip()
        for line in _render_data_prepper_template("logrotate-dlq.conf.j2").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    header, *body, closing = lines
    assert header.endswith("{")
    assert closing == "}"
    return header.removesuffix("{").strip(), body


def test_data_prepper_dlq_logrotate_rule_bounds_the_file() -> None:
    """The DLQ has no size limit of its own; logrotate caps it (#1239).

    copytruncate (not rename+create) is load-bearing: DP 2.16.0 opens the file once
    (CREATE+APPEND) and never reopens it, so a rename would leave DP writing into the
    rotated file. No `create`/`postrotate`: nothing could make DP reopen it.
    """
    path, directives = _logrotate_rule()
    assert path == DLQ_FILE
    assert sorted(directives) == sorted(
        [
            "su data-prepper data-prepper",
            "size 100M",
            "rotate 4",
            "copytruncate",
            "compress",
            "missingok",
            "notifempty",
        ]
    )


def test_data_prepper_dlq_rotation_runs_hourly_from_a_dedicated_config() -> None:
    """A size cap only bounds the file if logrotate runs often enough (#1239).

    Ubuntu 24.04's stock logrotate.timer is daily, so a dedicated hourly timer runs
    logrotate on a config kept OUT of /etc/logrotate.d (exactly one runner), and the
    configure half enables it.
    """
    ctx = _data_prepper_context()
    conf = ctx["data_prepper_dlq_logrotate_conf"]
    assert not conf.startswith("/etc/logrotate.d/")

    timer = _render_data_prepper_template("data-prepper-dlq-rotate.timer.j2")
    assert "OnCalendar=hourly" in timer.splitlines()

    service = _render_data_prepper_template("data-prepper-dlq-rotate.service.j2")
    exec_start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    state = ctx["data_prepper_dlq_logrotate_state"]
    assert exec_start == f"ExecStart=/usr/sbin/logrotate --state {state} {conf}"

    configure = yaml.safe_load(DATA_PREPPER_CONFIGURE.read_text(encoding="utf-8"))
    enabled = [t["ansible.builtin.systemd"] for t in configure if "ansible.builtin.systemd" in t]
    timer_task = {
        "name": "data-prepper-dlq-rotate.timer",
        "enabled": True,
        "state": "started",
        "daemon_reload": True,
    }
    assert timer_task in enabled


OPENSEARCH_ROLE = ANSIBLE_ROOT / "roles" / "opensearch"
OPENSEARCH_INDEX_TEMPLATE = OPENSEARCH_ROLE / "templates" / "index-template.json.j2"
OPENSEARCH_DEFAULTS = OPENSEARCH_ROLE / "defaults" / "main.yml"


def _opensearch_index_mappings() -> dict:
    """Render the logs-* index template with the role defaults; return its mappings."""
    defaults = yaml.safe_load(OPENSEARCH_DEFAULTS.read_text(encoding="utf-8"))
    context = {
        "opensearch_index_pattern": defaults["opensearch_index_pattern"],
        "opensearch_number_of_replicas": defaults["opensearch_number_of_replicas"],
    }
    source = OPENSEARCH_INDEX_TEMPLATE.read_text(encoding="utf-8")
    template = json.loads(_ansible_jinja_env().from_string(source).render(context))
    assert template["index_patterns"] == ["logs-*"]
    return template["template"]["mappings"]


def test_opensearch_index_template_disables_date_detection() -> None:
    """Dynamic date detection must be off on the logs-* template (#1230).

    With it on, the first document of a daily index fixed a date-looking MQ insert
    (`ibm_commentInsert2`) as `date`, and OpenSearch then rejected every later
    non-date value in that field with HTTP 400 for the rest of the day.
    """
    mappings = _opensearch_index_mappings()
    assert mappings["date_detection"] is False


def test_opensearch_index_template_keeps_declared_date_fields() -> None:
    """The declared time fields stay `date` with detection off (#1230).

    Dashboards uses `time` and Grafana uses `@timestamp` as the time field;
    `ibm_datetime` is the MQ record timestamp.
    """
    properties = _opensearch_index_mappings()["properties"]
    for field in ("@timestamp", "time", "ibm_datetime"):
        assert properties[field] == {"type": "date"}, field
    assert properties["message"] == {"type": "text"}
    assert properties["body"] == {"type": "text"}


def test_opensearch_index_template_maps_mq_comment_inserts_as_text() -> None:
    """MQ free-form string inserts map to text + .keyword, never date (#1230)."""
    dynamic_templates = _opensearch_index_mappings()["dynamic_templates"]
    entry = next(t["mq_comment_inserts"] for t in dynamic_templates if "mq_comment_inserts" in t)
    assert entry == {
        "match": "ibm_commentInsert*",
        "match_mapping_type": "string",
        "mapping": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
        },
    }


def test_alloy_journal_relabel_drops_own_log_shipping_units() -> None:
    """The Alloy journal relabel must DROP the log-shipping components' own units (#1029).

    Without a drop rule, Alloy's `loki.source.journal` re-scrapes Alloy's own
    `sending queue is full` errors (and Data Prepper's) and re-ships them to both
    Loki and the OpenSearch bridge — a self-amplifying feedback loop that grew to
    ~90% of all log volume under backpressure (epic .github#198). The
    `loki.relabel "journal"` block must carry an `action = "drop"` rule whose unit
    regex matches both `alloy.service` and `data-prepper.service`.
    """
    env = _ansible_jinja_env()
    # `bool` is an Ansible-native filter (not a Jinja builtin); the config uses it
    # to gate the OpenSearch fan-out. Register a faithful stand-in so the template
    # renders. `default` is a Jinja builtin and needs no registration.
    env.filters["bool"] = lambda v: (
        v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on", "t", "y")
    )
    context = {
        "inventory_hostname": "obs",
        "loki_push_url": "http://loki.obs:3100/loki/api/v1/push",
    }
    rendered = env.from_string(ALLOY_CONFIG.read_text(encoding="utf-8")).render(context)

    # A drop action must be present in the rendered config.
    assert re.search(r'action\s*=\s*"drop"', rendered), (
        'the Alloy journal relabel must carry an `action = "drop"` rule to break '
        f"the #1029 self-ingestion feedback loop; rendered config:\n{rendered}"
    )

    # The drop rule's unit regex must match BOTH log-shipping units. Pull the regex
    # literal that sits in the same `rule { ... }` block as the drop action.
    # The regex literal may be a double-quoted OR a backtick raw string (the latter is
    # required for an escaped `\.`, which is an invalid double-quoted River escape — #1068).
    drop_rule = re.search(
        r"rule\s*\{[^}]*?regex\s*=\s*[\"`](?P<re>[^\"`]+)[\"`][^}]*?action\s*=\s*\"drop\"[^}]*?\}",
        rendered,
        re.DOTALL,
    )
    assert drop_rule, (
        'could not find a `rule { ... regex = ... action = "drop" ... }` block in the '
        f"rendered Alloy config; rendered config:\n{rendered}"
    )
    unit_regex = drop_rule.group("re")
    compiled = re.compile(unit_regex)
    for unit in ("alloy.service", "data-prepper.service"):
        assert compiled.search(unit), (
            f"the #1029 drop regex {unit_regex!r} must match the log-shipping unit "
            f"{unit!r} so its own journal entries are never re-shipped"
        )


def test_parse_guard_catches_malformed_expression() -> None:
    """Regression fixture for the exact #774 bite: a literal `{{ … }}` with a
    Unicode ellipsis (as appeared in a shellcheck-disable comment) is a Jinja
    parse error. This asserts the guard above actually trips on that class —
    a guard that never fails is worthless."""
    env = _ansible_jinja_env()
    with pytest.raises(jinja2.TemplateSyntaxError):
        env.parse("# shellcheck disable {{ … }}\n")
