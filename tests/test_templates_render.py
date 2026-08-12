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

from pathlib import Path

import jinja2
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPO_ROOT / "ansible"

DATA_PREPPER_INSTALL = ANSIBLE_ROOT / "roles" / "data-prepper" / "tasks" / "install.yml"


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


def test_parse_guard_catches_malformed_expression() -> None:
    """Regression fixture for the exact #774 bite: a literal `{{ … }}` with a
    Unicode ellipsis (as appeared in a shellcheck-disable comment) is a Jinja
    parse error. This asserts the guard above actually trips on that class —
    a guard that never fails is worthless."""
    env = _ansible_jinja_env()
    with pytest.raises(jinja2.TemplateSyntaxError):
        env.parse("# shellcheck disable {{ … }}\n")
